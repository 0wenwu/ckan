# encoding: utf-8

'''
Migrates revisions into the activity stream, to allow you to view old versions
of datasets and changes (diffs) between them.

This should be run once you've upgraded to CKAN 2.9.

This script is not part of the main migrations because it takes a long time to
run, and you don't want it to delay a site going live again after an upgrade.
In the period between upgrading CKAN and this script completes, the Activity
Stream's view of old versions of datasets and diffs between them will be
incomplete - it won't show resources, extras or tags.

This script is idempotent - there is no harm in running this multiple times, or
stopping and restarting it.

We won't delete the revision tables in the database yet, since we haven't
converted the group, package_relationship to activity objects yet.

(In a future version of CKAN we will remove the 'package_revision' table from
the codebase. We'll need a step in the main migration which checks that
migrate_package_activity.py has been done, before it removes the
package_revision table.)
'''

# This code is not part of the main CKAN CLI because it is a one-off migration,
# whereas the main CLI is a list of tools for more frequent use.

from __future__ import print_function
import argparse
import sys
from six.moves import input

# not importing anything from ckan until after the arg parsing, to fail on bad
# args quickly.

_context = None


def get_context():
    from ckan import model
    import ckan.logic as logic
    global _context
    if not _context:
        user = logic.get_action(u'get_site_user')(
            {u'model': model, u'ignore_auth': True}, {})
        _context = {u'model': model, u'session': model.Session,
                    u'user': user[u'name']}
    return _context


def migrate_all_datasets():
    import ckan.logic as logic
    dataset_names = logic.get_action(u'package_list')(get_context(), {})
    num_datasets = len(dataset_names)
    for i, dataset_name in enumerate(dataset_names):
        print(u'{}/{} {}'.format(i + 1, num_datasets, dataset_name))
        migrate_dataset(dataset_name)


def migrate_dataset(dataset_name):
    # monkey patch the legacy versions of code back into CKAN - so it has the
    # revision functionality needed for this migration
    import ckan.lib.dictization.model_dictize as model_dictize
    try:
        import ckan.migration.revision_legacy_code as revision_legacy_code
    except ImportError:
        # convenient to look for it in the current directory if you just
        # download these files because you are upgrading an older ckan
        import revision_legacy_code
    model_dictize.package_dictize = \
        revision_legacy_code.package_dictize_with_revisions

    import ckan.logic as logic
    from ckan import model

    context = get_context()
    # 'hidden' activity is that by site_user, such as harvests, which are
    # not shown in the activity stream because they can be too numerous.
    # However these do have Activity objects, and if a hidden Activity is
    # followed be a non-hidden one and you look at the changes of that
    # non-hidden Activity, then it does a diff with the hidden one (rather than
    # the most recent non-hidden one), so it is important to store the
    # package_dict in hidden Activity objects.
    context[u'include_hidden_activity'] = True
    package_activity_stream = logic.get_action(u'package_activity_list')(
        context, {u'id': dataset_name})
    num_activities = len(package_activity_stream)
    if not num_activities:
        print(u'  No activities')

    context[u'for_view'] = False
    # Iterate over this package's existing activity stream objects
    for i, activity in enumerate(package_activity_stream):
        # e.g. activity =
        # {'activity_type': u'changed package',
        #  'id': u'62107f87-7de0-4d17-9c30-90cbffc1b296',
        #  'object_id': u'7c6314f5-c70b-4911-8519-58dc39a8e340',
        #  'revision_id': u'c3e8670a-f661-40f4-9423-b011c6a3a11d',
        #  'timestamp': '2018-04-20T16:11:45.363097',
        #  'user_id': u'724273ac-a5dc-482e-add4-adaf1871f8cb'}
        print(u'  activity {}/{} {}'.format(
              i + 1, num_activities, activity[u'timestamp']))

        # get the dataset as it was at this revision
        context[u'revision_id'] = activity[u'revision_id']
        # call package_show just as we do in package.py:activity_stream_item(),
        # only with a revision_id
        dataset = logic.get_action(u'package_show')(
            context,
            {u'id': activity[u'object_id'], u'include_tracking': False})
        # get rid of revision_timestamp, which wouldn't be there if saved by
        # during activity_stream_item() - something to do with not specifying
        # revision_id.
        if u'revision_timestamp' in (dataset.get(u'organization') or {}):
            del dataset[u'organization'][u'revision_timestamp']
        for res in dataset[u'resources']:
            if u'revision_timestamp' in res:
                del res[u'revision_timestamp']

        actor = model.Session.query(model.User).get(activity[u'user_id'])
        actor_name = actor.name if actor else activity[u'user_id']

        # add the data to the Activity, just as we do in activity_stream_item()
        data = {
            u'package': dataset,
            u'actor': actor_name,
        }
        # there are no action functions for Activity, and anyway the ORM would
        # be faster
        activity_obj = model.Session.query(model.Activity).get(activity[u'id'])
        if u'resources' in activity_obj.data.get(u'package', {}):
            print(u'    Full dataset already recorded - no action')
        else:
            activity_obj.data = data
            # print '    {} dataset {}'.format(actor_name, repr(dataset))
    if model.Session.dirty:
        model.Session.commit()
        print(u'  saved')
    print(u'All {} datasets are migrated'.format(len(package_activity_stream)))


def wipe_activity_detail():
    from ckan import model
    num_activity_detail_rows = \
        model.Session.execute(u'SELECT count(*) FROM "activity_detail";') \
        .fetchall()[0][0]
    if num_activity_detail_rows == 0:
        print(u'\nactivity_detail table is aleady emptied')
        return
    print(
        u'\nNow the migration is done, the history of datasets is now stored\n'
        'in the activity table. As a result, the contents of the\n'
        'activity_detail table will no longer be used after CKAN 2.8.x, and\n'
        'you can delete it to save space (this is safely done before or\n'
        'after the CKAN upgrade).'
        )
    response = input(u'Delete activity_detail table content? (y/n):')
    if response.lower()[:1] != u'y':
        sys.exit(0)
    from ckan import model
    model.Session.execute(u'DELETE FROM "activity_detail";')
    model.Session.commit()
    print(u'activity_detail deleted')


if __name__ == u'__main__':
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument(u'-c', u'--config', help=u'CKAN config file (.ini)')
    parser.add_argument(u'--dataset', help=u'just migrate this particular '
                        u'dataset - specify its name')
    args = parser.parse_args()
    assert args.config, u'You must supply a --config'
    try:
        from ckan.lib.cli import load_config
    except ImportError:
        # for CKAN 2.6 and earlier
        def load_config(config):
            from ckan.lib.cli import CkanCommand
            cmd = CkanCommand(name=None)

            class Options(object):
                pass
            cmd.options = Options()
            cmd.options.config = config
            cmd._load_config()
            return

    print(u'Loading config')
    load_config(args.config)
    if not args.dataset:
        migrate_all_datasets()
        wipe_activity_detail()
    else:
        migrate_dataset(args.dataset)

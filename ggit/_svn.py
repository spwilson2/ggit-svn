import os
import hashlib
import sqlite3

import locker
from _util import *

# Lower utility layer of svn.


def svn_info(dir_='.'):
    output = call_output('svn info "%s"' % dir_)

    # Call svn info returning metadata:
    keys = {'depth', 'revision'}

    pairs = {'depth':'infinity'}
    for line in output.splitlines():
        split = line.split(':')
        key = split[0].lower()
        val = ':'.join(split[1:])
        if key in keys:
            pairs[key] = val

    return pairs


# TODO Calls to Svn should be made through an SvnExecutor object.
class SvnCacheEntry(object):
    def __init__(self, tag, root, revision, depth):
        self.tag = tag
        self.revision = revision
        self.depth = depth
        self.root = root

    @property
    def path(self):
        return SvnCache._cache_path(self.root, self.tag)

    def update(self, revision):
        # Update the checkout. 
        # NOTE assumes the user has a lock if required.
        forward_check_call('svn update --force --accept working'
                ' --set-depth=infinity -r {rev}'
                ' "{path}"'.format(rev=revision, path=self.path))

    def copy_to(self, root):
        new_entry = SvnCacheEntry(self.tag, root, self.revision, self.depth)

        # Must acquire/create a lock in the newly created directory.
        # NOTE/XXX: This isn't 100% atomic, there could be an ABA problem here.
        check_call(['mkdir', '-p', root])
        with new_entry.wlock() as lock:
            check_call('cp -ra "%s" "%s"' % (self.path, root))

        return new_entry

    def checkout(self):
        # NOTE Assume lock is held
        forward_check_call('svn checkout --depth={depth} "{url}" "{loc}"'
                ''.format(depth=self.depth, url=self.tag.path, loc=self.path))

    def rlock(self):
        return locker.SvnCacheReaderLock(self.root)

    def wlock(self):
        return locker.SvnCacheWriterLock(self.root)


class SvnCache(object):
    def __init__(self, local_dir, extern_dir=None):
        self.local_dir = local_dir
        self.extern_dir = extern_dir

    def _get_entry(self, root, tag):
        # Create a SvnCacheEntry if it exists locally
        cache_path = self._cache_path(root, tag)
        if os.path.exists(cache_path):
            info = svn_info(cache_path)
            return SvnCacheEntry(tag, root, info['revision'], info['depth'])

    def get_local_entry(self, tag):
        return self._get_entry(self.local_dir, tag)

    def get_extern_entry(self, tag):
        if self.extern_dir is not None:
            return self._get_entry(self.extern_dir, tag)

    def _update_extern(self, tag):
        pass

    @staticmethod
    def _cache_path(dir_, tag):
        return os.path.join(dir_, tag.hash)

    def create(self, tag, rev):
        '''
        Create a local checkout of the given svn tag. If a checkout already
        exists..... TODO

        If a checkout does not exist, this cache manager will first check for
        an external cache to use.

        If the external cache has this tag cached it will be copied over to
        a local checkout. Before doing so, we will compare revisions of the
        external cache and the requested revision. If the requested revision is
        newer, the external cache will be updated to that revision before the
        copy.
        '''
        lcache = self.get_local_entry(tag)

        # Check if there exists a cache version.
        ecache = self.get_extern_entry(tag)
        if ecache is not None:
            with ecache.rlock() as lock:
                
                # If the revision is newer than the cached version was,
                # update the cached version in place.
                if ecache.revision < rev:
                    lock.upgrade()
                    ecache.update(rev)

                if lcache is not None:
                    return

                # Copy the external cache over to create a local cache
                lcache = ecache.copy_to(self.local_dir)
                if rev != lcache.revision:
                    lcache.update(rev)

                return

        new = False
        if lcache is None:
            lcache = SvnCacheEntry(tag, self.local_dir, rev, 'infinity')
            new = True

        with lcache.wlock():
            if new:
                lcache.checkout()

            if self.extern_dir is None:
                return

            # Copy the local cache into an external cache.
            lcache.copy_to(self.extern_dir)

    def switch_checkout(self, tag, checkout):
        cache = self.get_local_entry(tag)
        assert cache

        with Chdir(checkout):
            if os.path.exists('.svn'):
                shutil.rmtree('.svn')
            # Create a .svn dir and symlink the files because subversion won't
            # look at a symlink for its .svn dir.
            os.mkdir('.svn')
            os.mkdir('.svn/tmp')

            cache_svn = os.path.join(cache.path, '.svn')

            # Svn also has a tendency to remove the tmp dir at random times. It
            # does so by calling a rmdir routine, we will just let it do its thing
            # and not link this.
            dirs = list(os.listdir(cache_svn))
            if 'tmp' in dirs:
                dirs.remove('tmp')
            for f in dirs:
                os.symlink(os.path.join(cache_svn, f), os.path.join('.svn', f))

            # Run svn update to set the revision and depth.
            check_call('svn cleanup')
            forward_check_call('svn update --force --accept working'
                               ' --set-depth=infinity -r {rev}'
                               ''.format(rev=cache.revision))
            # Run svn revert to clean to a known svn state.
            check_call('svn revert -R .')

#
#
# Upper command layer of svn.
#
#

class SvnCacheTag(object):
    def __init__(self, path, ver=0):
        self.path = path

        h = hashlib.sha256(self.path)
        h.update(str(ver))
        self.hash = h.hexdigest()

def switch_checkout(ggit, tag, root, **kwargs):
    '''
    High-level command to create or switch the svn checkout to a SvnCacheTag in
    a given root directory. This command can utilize a local cache of svn
    checkouts so it isn't necessary to clone from the external svn server.

    :param ggit: An instance of the ggit configuration.

    :param tag: The reference to the svn checkout to switch to.

    :param root: The base directory of the checkout. (The location a .svn will
    be placed.)


    Keyword only params:

    :param rev: The revision to set the checkout to, if None the revision is
    not changed.
    '''

    # NOTE: There are a couple strange details when it comes to implementing
    # a fast switch call for svn.
    #
    # One option would be to use svn switch.  There are a couple problems with
    # this approach.  First, .svn doesn't do a good job of keeping a lot of
    # state around, so on very different branches a switch will effectively be
    # a new clone.  Second, switch is still pretty broken when it comes to
    # switching svn externals across branches.  This often leads to a broken
    # .svn and would confuse users.
    #
    # Rather than using the `svn switch` subcommand, we keep individual .svn
    # checkouts for each branch. We then swap these out when switching
    # branches.
    #
    # This approach has its own issues. Subversion doesn't use .svn if it is
    # a symlink. So, rather than being able to create a single symlink from
    # .svn to .svn, we symlink all files in the .svn folder.
    #
    # The one issue that could arise with this approach is if subversion
    # creates new files in the .svn folder they will be deleted on switch. We
    # only maintain the folders and files from the original svn checkout. For
    # example, the .svn/tmp directory will be removed every time the branch is
    # switched.

    rev = pop_kwarg(kwargs, 'rev') # Revision to set the svn checkout to. 

    # Create a local copy if it doesn't already exist.
    cache = SvnCache(ggit.ggit_local_cache_dir, ggit.ggit_extern_cache_dir)
    lcache = cache.get_local_entry(tag)
    if not lcache:
        # Use the latest rev upstream if no rev given.
        if rev is None:
            info = svn_info(tag.path)
            rev = info['revision']

        cache.create(tag, rev)
        lcache = cache.get_local_entry(tag)
        assert lcache

    cache.switch_checkout(tag, root)

# TODO Add a cleanup command to remove all lockfiles.

if __name__ == '__main__':
    class GG():
        ggit_local_cache_dir = '/tmp/local-cache'
        ggit_extern_cache_dir = '/tmp/extern-cache'

    import pdb; pdb.set_trace()
    tag = SvnCacheTag('http://rtosvc/trunk/rtos/rtos_val/psival/tests')
    switch_checkout(GG, tag, '/tmp/checkout')

    #cache = SvnCache('/tmp', '/tmp/cache')
    #cache.create(tag, 281887)

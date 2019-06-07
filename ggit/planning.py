import os
import hashlib
import sqlite3

import locker

# Lower utility layer of svn.

class SvnCacheEntry(object):
    def __init__(self, tag, root, revision, depth):
        self.tag = tag
        self.revision = revision
        self.depth = depth

    def update(self, revision):
        pass #TODO

    def copy_to(self, root):
        # TODO Must acquire/create a lock in the newly created directory.
        pass

    def checkout(self, output):
        pass

    def rlock(self):
        return locker.SvnCacheReaderLock(self.root)

    def wlock(self):
        return locker.SvnCacheWriterLock(self.root)


class SvnCache(object):
    def __init__(self, local_dir, extern_dir=None):
        self.local_dir = local_dir
        self.extern_dir = extern_dir

    def get_local_entry(self, tag):
        # TODO Create a SvnCacheEntry if it exists locally
        return None

    def get_extern_entry(self, tag):
        # TODO Create a SvnCacheEntry if it exists.
        return None

    def _update_extern(self, tag):
        pass

    @staticmethod
    def _cache_path(dir_, tag):
        return os.path.join(dir_, hash(tag))

    def create(self, tag, rev):
        lcache = self.get_local_entry()
        if lcache is None:
            return

        # Check if there exists a cache version.
        ecache = self.get_extern_entry()
        if ecache is not None:
            with ecache.rlock() as lock:
                
                # If the revision is newer than the cached version was,
                # update the cached version in place.
                if ecache.revision > rev:
                    lock.upgrade()
                    ecache.update(r)

                # Copy the external cache over
                ecache.copy_to(self._cache_path(self.local_dir, tag))
                lock.unlock()
                return

        lcache = SvnCacheEntry(tag, self.local_dir, rev, 'infinity')
        with lcache.wlock():
            lcache.checkout(self._cache_path(self.local_dir, tag))

            if self.extern_dir is None:
                return

            # Copy the local cache into an external cache.
            lcache.copy_to(self.extern_dir)

    def switch(self, tag, rev=None):
        pass #TODO

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

        self.hash_ = h.hexdigest()

    def __hash__(self):
        return self.hash_


def switch_checkout(ggit, tag, **kwargs):
    '''
    :param tag: The reference to the svn checkout to switch to.

    Keyword only params:

    :param rev: The revision to set the checkout to, if None the revision is not changed.
    '''
    rev = pop_kwarg(kwargs, 'rev') # Revision to set the svn checkout to. 

    # Create a local copy if it doesn't already exist.
    cache = SvnCache(ggit.ggit_local_cache_dir, ggit.ggit_extern_cache_dir)
    if not cache.exists(tag):
        cache.create(tag, rev)

    # Change the local cache to point to this cache
    cache.switch(tag, rev)

def pop_kwarg(kwargs, arg, default=None):
    if arg in kwargs:
        return kwargs.pop(arg)
    return default

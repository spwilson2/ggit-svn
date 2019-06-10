import os
import hashlib
import sqlite3

# Lower utility layer of svn.

class SqliteAbortExcpetion(Exception):
    pass

class LockAcquireException(Exception):
    pass

class SqliteTransaction(object):
    def __init__(self, con):
        self.con = con

    def abort(self):
        raise SqliteAbortExcpetion()

    def __enter__(self):
        self.con.__enter__()
        return self, self.con

    def __exit__(self, typ, *args):
        if typ == SqliteAbortExcpetion:
            self.con.execute('ROLLBACK TRANSACTION;')
            return True
        self.con.__exit__(typ, *args)

# Locks to use a sqlite database to control access.
#
# Readers append their pid to reader field
# Writers write their pid to pid field.
# To prevent ABA problem, tid field is incremented in every transaction.
class SvnCacheLock(object):
    lfilename = '.lockfile'

    _table = 'ggit_svn_cache'
    _reader_field = 'readers'
    _writer_field = 'writer'
    _tid_field = 'tid'

    def __init__(self, root):
        self._con = None
        self.root = root

    @property
    def lockfile(self):
        return os.path.join(self.root, self.lfilename)

    @property
    def _lockid(self):
        return '-%s-' % os.getpid()

    def _initialize_database(self):
        map = {
                'table':self._table, 
                'writer': self._writer_field, 
                'reader': self._reader_field
                }

        with SqliteTransaction(self._con) as (trans, con):
            # Create table
            con.execute('CREATE TABLE IF NOT EXISTS {table} ('
                    'id INTEGER PRIMARY KEY CHECK (id = 0), '
                    '{reader} TEXT, '
                    '{writer} TEXT, '
                    'tid TEXT'
                    ')'.format(**map))

            # Insert single entry
            con.execute('INSERT INTO %s'
                        ' SELECT 0, "","","0"'
                        ' WHERE NOT EXISTS('
                        '  SELECT 1 FROM %s where id = 0);' % (self._table, self._table))

    def _connect(self):
        if self._con is None:
            self._con = sqlite3.connect(self.lockfile)

    def _transaction(self):
        self._connect()
        self._initialize_database()
        #cur = self._con.cursor()
        #cur.execute('BEGIN DEFERRED TRANSACTION')
        return SqliteTransaction(self._con)

    def _select_data(self, cur):
        row = cur.execute('SELECT * FROM ' + self._table)
        row = row.fetchone()
        return {
                'id': row[0],
                'readers': str(row[1]),
                'writer': str(row[2]),
                'tid': int(row[3]),
                }

    def _write_data(self, cur, data):
        data['tid'] += 1
        row = cur.execute("UPDATE %s SET readers = '%s', writer = '%s', tid = '%s' WHERE id = 0" % (self._table, data['readers'], data['writer'], data['tid']))
        pass

    def __enter__(self):
        self.lock()
        return self

    def __exit__(self, *args):
        self.unlock()

class SvnCacheReaderLock(SvnCacheLock):
    def __init__(self, *args, **kwargs):
        SvnCacheLock.__init__(self, *args, **kwargs)
        self._upgraded = False

    def lock(self):
        with self._transaction() as (trans, con):
            data = self._select_data(con)

            if data['writer']:
                raise LockAcquireException()

            assert self._lockid not in data['readers']

            data['readers'] = data['readers'] + self._lockid
            self._write_data(con, data)

    def unlock(self):
        with self._transaction() as (trans, con):
            data = self._select_data(con)

            assert self._lockid in data['readers']
            assert self._lockid not in data['writer']
            data['readers'] = data['readers'].replace(self._lockid, '')

            self._write_data(con, data)

    def upgrade(self):
        with self._transaction() as (trans, con):
            data = self._select_data(con)

            if data['writer']:
                raise LockAcquireException()

            assert self._lockid in data['readers']

            data['readers'] = data['readers'].replace(self._lockid, '')

            if data['readers']:
                raise LockAcquireException()

            data['writer'] = self._lockid
            self._write_data(con, data)

            self._upgraded = True
            return SvnCacheWriterLock(self.root)

class SvnCacheWriterLock(SvnCacheLock):
    def lock(self):
        with self._transaction() as (trans, con):
            data = self._select_data(con)

            if data['writer']:
                raise LockAcquireException()

            if data['readers']:
                raise LockAcquireException()

            data['writer'] = self._lockid
            self._write_data(con, data)

    def unlock(self):
        with self._transaction() as (trans, con):
            data = self._select_data(con)

            assert self._lockid == data['writer']
            data['writer'] = ''

            self._write_data(con, data)

if __name__ == '__main__':
    lock = SvnCacheReaderLock('/tmp')
    lock.lock()
    lock.unlock()
    lock.lock()
    lock.unlock()


    lock.lock()
    wlock = lock.upgrade()
    wlock.unlock()
    pass

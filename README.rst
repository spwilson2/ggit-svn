
Overview
========

ggit is a script to simplify working with both a .git and .svn repository in the same working directory.


Comamnds
^^^^^^^^

Clone
-----

Clone is used to clone a git repository with git-svn branches.

Under the hood clone does the following:

1. Verifies the remote has a ggit-config branch.

Switch
------

Switch is used to change both the git index and svn revision/url.

Under the hood it does the following:

1. Search for the git-svn commit message in the git log of hashish.
   Save the url and revision number.
2. Git checkout the given hashish.
3. Replace the symlinks in the .svn folder to those of the url from the git-svn log entry.
4. Run svn update to set the revision number of the repo to match the lastest git-svn commit on the hashish.




Git Repository Setup
====================

* All git-svn tracked branches will be in the *git-svn* folder on the main repo.
* The git repository will have a branch *ggit-config* which will contain a config file.


ggit-config
===========

A ggit repository uses a configuration file to setup and manage git-svn branches.
The config uses the same format as the git config.
This file format closely resembles .ini/.toml files.

ggit will only look at svn-remote sections.


There are two required configuration attributes for svn-remotes.

* ``url`` is the base url for the subversion respoitory.
* ``fetch`` options specify different branches to create from the svn repository
  the format is::
      <path from svn root>:refs/remotes/<branch-name>

As a complete example::

    [svn-remote "svn"]
            url = file:///srv/svn
            fetch = trunk/rtos:refs/remotes/git-svn/trunk
            fetch = branches/ap/trunk/rtos:refs/remotes/git-svn/aptrunk

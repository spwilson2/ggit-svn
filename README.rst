

Git Repository Setup
====================

* All git-svn tracked branches will be in the *git-svn* folder on the main repo.
* The git repository will have a branch *ggit-config* which will contain a config file.


ggit-config
===========

A ggit repository uses a configuration file to setup and manage git-svn branches.
The config is setup as a python ConfigParser file.
This file format closely resembles .ini/.toml files.


E.g. ::
    
    [base]
    url = http://rtosvc

    [branch.trunk]
    path = trunk/rtos

    [branch.aptrunk]
    path = branches/ap/trunk/rtos

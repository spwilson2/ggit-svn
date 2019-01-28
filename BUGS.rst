Known Bugs
==========

* The ggit switch silently partially fails if there is a folder with a .svn in it (besides the base) on switch.

Cherry picking from a git-svn branch
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

ggit looks at the latest git-svn commit in the log to find the svn URL we are on.
Because of this you can run into issues when cherry-picking between git-svn branches.
To fix this issue, you will need to change cherry-picked commit messages to remove the git-svn tag.

For example::

    # We are on branch trunk and want to cherry pick the most recent commit from svn/devel
    git cherry-pick svn/devel

    # Edit the TODO LIST to "reword" for all commits
    git rebase -i HEAD^
     # or since we only cherry-picked a single commit, a git --amend would work as well.


.. Developer-Note:
    We could work around this issue by also peeking at the earliest git log message.
    However, if we do so, and a git-svn branch was rebased onto another (to
    show their related history) then we would run into the same issue without
    a simple workaround.



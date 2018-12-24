#!/bin/bash

set -ex

cleanup() {
  rm -rf /tmp/git-test
}

cleanup

mkdir /tmp/git-test
cd /tmp/git-test

mkdir git-repo
pushd git-repo
  git init --bare
popd

# Create a new git repository from an svn repository

mkdir git-1
pushd git-1

  ggit init file:///srv/svn trunk/rtos:trunk branches/ap/trunk/rtos:aptrunk
  git remote add origin /tmp/git-test/git-repo
  ggit push --force origin

popd


# Clone from that new ggit repository
ggit clone /tmp/git-test/git-repo git-2

pushd git-2

#
ggit switch origin/svn/aptrunk
svn info | grep -q ap/trunk

# Reinitialize, but on trunk now.
ggit init file:///srv/svn trunk/rtos:trunk
git svn fetch

ggit switch git-svn/svn/trunk
svn info | grep -q -v ap/trunk
ggit sync
ggit generate-ignore

ggit push origin

ggit init file:///srv/svn trunk/rtos:trunk branches/ap/trunk/rtos:aptrunk
ggit push origin

popd

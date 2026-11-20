# hanthor/hummingbird-rpms

A mirror of <https://gitlab.com/redhat/hummingbird/rpms>, read-only from GitLab.
It is not an official Hummingbird repository, and nothing syncs it
automatically. Branches other than this one (`mirror-info`) and `utah/*` are
upstream's, as of the last refresh.

It stages Project Bluefin Utah's Secure Boot submissions as GitHub pull
requests for Hummingbird maintainers to review. The canonical place to merge
anything is still GitLab.

Refresh (updates upstream branches and tags; keeps `mirror-info`, `utah/*` and open PR branches):

```bash
git clone --mirror https://gitlab.com/redhat/hummingbird/rpms.git /tmp/hb-rpms.git && git -C /tmp/hb-rpms.git push --force https://github.com/hanthor/hummingbird-rpms.git 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'
```

# Maintainer release guide

The release workflow is intentionally inert for publishing until the repository
variable `D3DPLOT_RELEASES_ENABLED` is set to `true`. **Do not enable it or
publish a release until the release prerequisites in the architecture plan are
met.

## One-time GitHub and PyPI setup

1. Create GitHub environments named `testpypi` and `pypi`. Add required
   reviewers or other protection rules appropriate for publishing.
2. Configure a Trusted Publisher for the `d3dplot` project on TestPyPI:
   GitHub owner/repository `V-Chavarrias/Delft3D_plot`, workflow
   `.github/workflows/release.yml`, environment `testpypi`.
3. Configure the corresponding Trusted Publisher on PyPI with the same
   repository and workflow, and environment `pypi`.
4. Keep publishing credentials out of GitHub secrets. The workflow uses
   GitHub's OIDC identity token and the PyPA publishing action.
5. Set the repository variable `D3DPLOT_RELEASES_ENABLED` to `true` when the
   first release is ready to be published.

The workflow builds an sdist and wheel for every `v*` tag and checks that the
tag matches `project.version` in `pyproject.toml`. A prerelease tag (for example
`v1.0.0rc1`) publishes only to TestPyPI. A final `vX.Y.Z` tag publishes to
TestPyPI first, then PyPI, and creates a GitHub release using that version's
`CHANGELOG.md` section. The TestPyPI upload is a real prerelease publication,
not a no-upload dry run; install and smoke-test it from TestPyPI before tagging
the final version.

## Release procedure

1. Confirm CI and the strict documentation build pass on the release commit.
   Verify all release prerequisites in the architecture plan.
2. Use Commitizen to bump the package version and generate its changelog entry.
   For a release candidate, use `uv run cz bump --prerelease rc`; for the final
   release, use the intended version explicitly, for example
   `uv run cz bump 1.0.0`. Commitizen changes `project.version` but not
   `uv.lock`, so run `uv lock` and amend the bump commit before tagging.
   Confirm with `uv lock --locked`; CI fails otherwise. Replace the generated
   changelog section with the curated text, then review the commit and tag.
3. Push the release commit and its tag. The tag starts the workflow; no package
   is published from a branch or pull request.
4. For a release candidate, install the package from TestPyPI in a clean
   environment and verify its import and CLI before proceeding. The final-tag
   workflow also publishes to TestPyPI before attempting PyPI publication.
   TestPyPI never accepts a filename it already has, so the TestPyPI step uses
   `skip-existing`. Do not re-tag a version that was already uploaded from an
   older commit: a re-run would then publish a build that differs from the tag.
   Reject any superseded waiting run in the Actions UI rather than approving it.
5. For a final version, confirm the `pypi` environment approval and check the
   resulting GitHub release and PyPI page.

If publishing is not yet enabled, tag workflows build and retain the
distributions but skip all publishing and GitHub-release jobs.

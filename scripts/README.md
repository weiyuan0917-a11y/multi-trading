# Community scripts

- `start-api.ps1`: starts the local Community FastAPI server.
- `start-frontend.ps1`: starts the local research/Paper UI.
- `check-community-boundary.ps1`: checks that no configured Pro-only asset or
  real-execution marker has re-entered the public source tree.

Run the strict boundary gate and tests before publishing:

```powershell
./scripts/check-community-boundary.ps1 -Strict
python -m pytest -q tests
```

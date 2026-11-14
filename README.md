# Footprints

Footprints is a local app for AI discovery, evidence insights, and passport workflows. The current release provides a synthetic discovery demo and local model passport creation and verification. [Forkit Core](https://github.com/Forkit-Dev-Core/Forkit_Dev) creates each passport, assigns its ID, and verifies its identity.

## Create and verify a model passport

### Windows download

If a Windows build is listed on [GitHub Releases](https://github.com/Forkit-Dev-Core/footprints/releases), download **Footprints-Windows-x64.zip**, extract the whole archive, and double-click **Start Footprints.cmd**. The archive includes Python, the application dependencies, and a sample file to try. See [Windows preview releases](docs/windows-release.md) for the maintainer workflow.

### Windows quick start

With Python 3.10 or later and Git available in PowerShell, run this from the folder where you want to keep Footprints.

```powershell
cmd.exe /d /c "git clone --depth 1 https://github.com/Forkit-Dev-Core/footprints.git && cd footprints && python -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt && .venv\Scripts\python.exe serve.py"
```

The command creates a local checkout, installs its pinned Forkit Core dependency in a virtual environment, and starts the local server. Press Ctrl+C to stop it. To start it again, open the `footprints` folder and run `.\.venv\Scripts\python.exe serve.py`.

### From an existing checkout

From a checkout of this repository, use Python 3.10 or later and Git to install the pinned Core dependency in a virtual environment.

In Windows PowerShell, call the virtual environment's Python directly.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe serve.py
```

On macOS or Linux, activate the virtual environment before installing and running Footprints.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python serve.py
```

Open [Footprints on this device](http://127.0.0.1:8765/footprints/). Choose a nonempty model artifact up to 64 MiB, enter its name, version, creator, task, and architecture, then select **Create and verify passport**. To try it with synthetic data, choose [sample-artifact.txt](scripts/sample-artifact.txt) from the checkout or extracted Windows ZIP.

For that example, enter `footprints-sample`, `1.0.0`, and `Footprints example`. Choose **Other** for both task and architecture.

The browser computes SHA-256 from the selected file. Core receives the hash, filename, and form metadata, and stores a passport in `~/.forkit/footprints`. Use `--registry-root` to choose another local directory. Verification checks that Core can derive the same ID from the stored identity fields. The UI displays the submitted file hash and the returned passport ID.

You can also open `index.html` directly to try the simulated discovery example. That example uses fictional signals and does not scan your device.

## Share an example

Use the simulated discovery page and the synthetic sample file for public demos, screenshots, and bug reports. A local passport can contain the filename and identity fields you enter. Keep local registry files and real identifiers private.

## Run the checks

Use Node.js 24 for the discovery tests. The Python integration tests use Core's real local registry in a temporary directory. Use the same virtual environment as above. In PowerShell, replace `python` below with `.\.venv\Scripts\python.exe`.

```sh
node --test
python -m pip install httpx
python -m unittest discover -s tests
```

## Contribute

Pick a [good first issue](https://github.com/Forkit-Dev-Core/footprints/labels/good%20first%20issue), report a problem in [Issues](https://github.com/Forkit-Dev-Core/footprints/issues), or suggest an idea in [Discussions](https://github.com/Forkit-Dev-Core/footprints/discussions). You can contribute code, tests, documentation, or examples. The [contributor guide](CONTRIBUTING.md) explains how to get a change reviewed.

Use the [community page](docs/community.md) to share demos, writeups, workshop resources, and lessons others can build on.

## License

This project is available under the [MIT License](LICENSE). Forkit Core is a separate dependency under its [Apache 2.0 license](https://github.com/Forkit-Dev-Core/Forkit_Dev/blob/main/LICENSE).

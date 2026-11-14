# Footprints

Footprints is a local app for AI discovery, evidence insights, and passport workflows. It finds supported AI tools and repository agent setups on your device. [Forkit Core](https://github.com/Forkit-Dev-Core/Forkit_Dev) creates each passport and verifies its identity fields.

## Get started

### Windows download

Download **Footprints-Windows-x64.zip** from [GitHub Releases](https://github.com/Forkit-Dev-Core/footprints/releases), extract it, and double-click **Start Footprints.cmd**. It opens Footprints in your browser. The ZIP includes Forkit Core, Python, and a sample file. See [Windows preview releases](docs/windows-release.md) for the maintainer workflow.

These device and repository discovery instructions describe this source branch. The existing Windows preview uses simulated discovery. When a Windows ZIP includes live discovery, open **Device**, select **Scan this device**, then choose **Create passport** on a supported installation. Windows scans can find supported processes, commands, and extensions. Installed-app inventory and publisher signature checks are currently available on macOS.

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

To register a model file, open [Footprints on this device](http://127.0.0.1:8765/footprints/) and choose **Passports → Register a model file**. Choose a nonempty file up to 64 MiB, enter its details, then select **Create and verify passport**. Use the synthetic [sample-artifact.txt](scripts/sample-artifact.txt) from the checkout or extracted Windows ZIP for a public example.

For that example, enter `footprints-sample`, `1.0.0`, and `Footprints example`. Choose **Other** for both task and architecture.

The browser computes SHA-256 from the selected file. Core receives the hash, filename, and form metadata, and stores a passport in `~/.forkit/footprints`. Use `--registry-root` to choose another local directory. Verification checks that Core can derive the same ID from the stored identity fields. The UI displays the submitted file hash and the returned passport ID.

You can also open `index.html` directly to try the simulated discovery example. That example uses fictional signals and does not scan your device.

Use the simulated page and a registry containing only synthetic passports for screenshots and reports. Print hides the live workspace. See [what Footprints stores locally](docs/passports.md#local-storage-and-sharing).

## Scan this device

With `python serve.py` running, the page shows **Scan this device**. It reads app bundle IDs, process names and parents, and editor and browser extension IDs on this device, and matches them with the same rules as the sample. It never reads conversations or history. The command lines of `node` and `python` processes are only compared in memory with known tool names, and the results contain no paths, arguments or user names. Nothing is stored and nothing leaves the device. [docs/proposals/device-discovery.md](docs/proposals/device-discovery.md) lists exactly what is read, and [docs/evidence-format.md](docs/evidence-format.md) explains how evidence becomes a result.

## Track repositories

**Scan repositories** lists the git repositories cloned on this device: where they are, their origin, when they were cloned and last active, and which AI agent files they contain (names, counts and a fingerprint of their contents; the text itself is never stored or shown). Each scan is saved locally, so the next one shows new, changed and missing repositories. See [docs/repositories.md](docs/repositories.md).

## Agent passports

Each discovered repository agent setup can get a Forkit Core **agent passport**. The Passports tab shows whether the record's identity fields match the saved ID and whether the measured setup files still match their fingerprint. See [the passport checks](docs/passports.md).

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

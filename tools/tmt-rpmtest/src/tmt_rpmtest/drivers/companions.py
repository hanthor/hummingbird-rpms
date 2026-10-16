"""Own companion containers and their private network as one scope."""

import json
import re
import subprocess
import uuid
from contextlib import ExitStack, contextmanager

from tmt_rpmtest.drivers import LABEL
from tmt_rpmtest.drivers.persistent import ContainerProcess, validate_command


def remove_network(runtime, name):
    exists = runtime._process(
        [runtime.executable, "network", "exists", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        interruptible=False,
    )
    if exists.returncode != 1:
        exists.check_returncode()
        runtime._command("network", "rm", name, interruptible=False)


@contextmanager
def companions(runtime, services, limits, directory, timeout, *, internet=False):
    if not isinstance(internet, bool):
        raise ValueError("Internet access must be explicitly True or False.")
    for name, spec in services.items():
        if name == "subject" or not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
            raise ValueError(f"Invalid or reserved companion name: {name!r}")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", spec.image):
            raise ValueError("Companion images must use a sha256 digest.")
        validate_command(spec.ready or ["probe"], timeout)
        if spec.command:
            validate_command(spec.command, timeout)
    network = f"tmt-rpmtest-{uuid.uuid4().hex}"
    processes = []
    with ExitStack() as cleanup:
        cleanup.callback(remove_network, runtime, network)
        options = [] if internet else ["--internal"]
        runtime._command(
            "network", "create", *options, "--label", f"{LABEL}={runtime.owner}", network
        )
        (directory / "network.json").write_text(
            json.dumps({"name": network, "internal": not internet, "internet": internet}) + "\n"
        )
        for name, spec in services.items():
            work = directory / name
            work.mkdir()
            (work / "container.json").write_text(
                json.dumps(
                    {
                        "image": spec.image,
                        "command": spec.command,
                        "ready": spec.ready,
                        "user": spec.user,
                    },
                    indent=2,
                )
                + "\n"
            )
            runtime.pull_image(spec.image)
            container = f"tmt-rpmtest-{name}-{uuid.uuid4().hex}"
            cleanup.callback(
                runtime._command,
                "rm",
                "--force",
                "--ignore",
                container,
                timeout=30,
                interruptible=False,
            )
            options = [
                "create",
                "--name",
                container,
                "--pull=never",
                "--network",
                network,
                "--network-alias",
                name,
                "--label",
                f"{LABEL}={runtime.owner}",
                "--image-volume=ignore",
                "--security-opt=no-new-privileges",
            ]
            for key, option in (
                ("cpus", "--cpus"),
                ("memory", "--memory"),
                ("pids", "--pids-limit"),
            ):
                options += [option, str(limits[key])]
            if spec.user is not None:
                options += ["--user", spec.user]
            for key, value in spec.environment.items():
                options += ["--env", f"{key}={value}"]
            if spec.command:
                options += ["--entrypoint", spec.command[0]]
            runtime._command(*options, spec.image, *spec.command[1:])
            process = ContainerProcess(runtime, container, work)
            cleanup.callback(process.close)
            runtime._command("start", container)
            process.require_running()
            processes.append((name, process, spec.ready))
        for _, process, ready in processes:
            process.require_running()
            if ready:
                process.wait_ready(ready, timeout)
        yield network, {name: process for name, process, _ in processes}
        for _, process, _ in processes:
            process.require_running()

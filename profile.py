"""Junction development testbed on CloudLab.

Two bare-metal nodes on a direct experiment link:
  node0 - Junction dev/build node (runs iokerneld + junction_run)
  node1 - workload / client node (load generators, iperf3, etc.)

Both nodes get Junction cloned to /local/junction and the dependency install
(scripts/install.sh) run in the background. Progress: /local/setup.log,
completion marker: /local/setup.done
"""

import geni.portal as portal
import geni.rspec.pg as rspec

pc = portal.Context()
request = pc.makeRequestRSpec()

pc.defineParameter(
    "node0_type",
    "Node 0 (Junction) hardware type",
    portal.ParameterType.NODETYPE,
    "c6525-25g",
)
pc.defineParameter(
    "node1_type",
    "Node 1 (workload) hardware type",
    portal.ParameterType.NODETYPE,
    "c6525-25g",
)
# Cluster must match the node types: c6525-25g/xl170 -> utah, c220g5/c220g2 -> wisc,
# c6420 -> clemson.
pc.defineParameter(
    "cluster",
    "CloudLab cluster",
    portal.ParameterType.STRING,
    "utah",
    [("utah", "Utah"), ("wisc", "Wisconsin"), ("clemson", "Clemson")],
)
pc.defineParameter(
    "junction_repo",
    "Junction git repo",
    portal.ParameterType.STRING,
    "https://github.com/JunctionOS/junction.git",
)
pc.defineParameter(
    "junction_branch", "Junction branch", portal.ParameterType.STRING, "main"
)
pc.defineParameter(
    "build_junction", "Build Junction after install", portal.ParameterType.BOOLEAN, True
)
pc.defineParameter(
    "link_bw_kbps",
    "Link bandwidth (Kbps, 0 = default)",
    portal.ParameterType.INTEGER,
    0,
    advanced=True,
)

params = pc.bindParameters()
pc.verifyParameters()

IMAGE = "urn:publicid:IDN+%s.cloudlab.us+image+emulab-ops:UBUNTU24-64-STD" % params.cluster
CM = "urn:publicid:IDN+%s.cloudlab.us+authority+cm" % params.cluster

SETUP_COMMON = r"""
set -x
exec > >(sudo tee -a /local/setup.log) 2>&1
sudo mkdir -p /local && sudo chown $(id -u):$(id -g) /local
cd /local
[ -d junction ] || git clone --branch {branch} {repo} junction
cd junction
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential
{extra}
# Rust (nightly, per Junction README)
if [ ! -x $HOME/.cargo/bin/rustc ]; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain=nightly
fi
./scripts/install.sh
{build}
touch /local/setup.done
"""

WORKLOAD_EXTRAS = r"""
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  iperf3 wrk memcached libmemcached-tools redis-tools netperf \
  linux-tools-common linux-tools-generic htop
"""


def setup_cmd(extra=""):
    build = "./scripts/build.sh" if params.build_junction else "true"
    body = SETUP_COMMON.format(
        branch=params.junction_branch,
        repo=params.junction_repo,
        build=build,
        extra=extra,
    )
    # Run detached so the profile instantiates immediately.
    script = body
    return "nohup bash -c '%s' >/dev/null 2>&1 &" % script.replace("'", "'\\''")


# ---- Node 0: Junction dev node ----
node0 = request.RawPC("node0")
node0.hardware_type = params.node0_type
node0.component_manager_id = CM
node0.disk_image = IMAGE
iface0 = node0.addInterface("data0")
iface0.addAddress(rspec.IPv4Address("10.10.1.1", "255.255.255.0"))
node0.addService(rspec.Execute(shell="bash", command=setup_cmd()))

# ---- Node 1: workload node ----
node1 = request.RawPC("node1")
node1.hardware_type = params.node1_type
node1.component_manager_id = CM
node1.disk_image = IMAGE
iface1 = node1.addInterface("data0")
iface1.addAddress(rspec.IPv4Address("10.10.1.2", "255.255.255.0"))
node1.addService(rspec.Execute(shell="bash", command=setup_cmd(WORKLOAD_EXTRAS)))

# ---- Experiment link ----
link = request.Link("data-link")
link.addInterface(iface0)
link.addInterface(iface1)
if params.link_bw_kbps:
    link.bandwidth = params.link_bw_kbps

pc.printRequestRSpec(request)

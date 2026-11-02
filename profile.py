"""Junction + PostgreSQL testbed on CloudLab.

One or two bare-metal nodes:
  node0 - Junction / Postgres server node (iokerneld + junction_run + postgres)
  node1 - optional workload / client node (pgbench and other load generators),
          on a direct experiment link with node0

Each node clones Junction to /local/junction, install its dependencies and the
PostgreSQL build dependencies, and (optionally) build Junction, all in the
background. Progress: /local/setup.log. When it finishes, /local/setup.done
(success) or /local/setup.failed (look at the log) appears.

Rust is installed to /local/rust and /local/junction is made writable by all
users, so the build works no matter which account the setup runs as.
"""

import geni.portal as portal
import geni.rspec.pg as rspec

# Which cluster each node type lives on. "auto" uses this to pick the cluster,
# and the profile refuses a node type that does not exist on the chosen one.
NODE_CLUSTER = {
    # Utah
    "c6525-25g": "utah", "c6525-100g": "utah", "c6620": "utah",
    "d6515": "utah", "d750": "utah", "d760": "utah", "d7615": "utah",
    "xl170": "utah", "m510": "utah", "m400": "utah",
    # Clemson
    "r6615": "clemson", "r6525": "clemson", "r7525": "clemson",
    "r650": "clemson", "c6420": "clemson", "c6320": "clemson",
    "c8220": "clemson", "c8220x": "clemson", "ibm8335": "clemson",
    "c4130": "clemson",
    # Wisconsin
    "c220g1": "wisc", "c220g2": "wisc", "c220g5": "wisc",
    "c240g1": "wisc", "c240g2": "wisc", "c240g5": "wisc",
    "d7525": "wisc", "d8545": "wisc", "sm110p": "wisc", "sm220u": "wisc",
}

pc = portal.Context()
request = pc.makeRequestRSpec()

pc.defineParameter(
    "num_nodes",
    "Number of nodes",
    portal.ParameterType.INTEGER,
    1,
    [(1, "1 (node0 only; run pgbench locally)"), (2, "2 (node0 + workload node1)")],
)
pc.defineParameter(
    "node0_type",
    "Node 0 (Junction + Postgres) hardware type",
    portal.ParameterType.NODETYPE,
    "c6620",
    longDescription="c6620 (Utah): Intel Xeon Gold 5512U, NVMe, Intel E810. "
    "Alternative: r6615 (Clemson, AMD EPYC 9354P, Mellanox CX-6).",
)
pc.defineParameter(
    "node1_type",
    "Node 1 (workload) hardware type",
    portal.ParameterType.NODETYPE,
    "c6620",
)
pc.defineParameter(
    "cluster",
    "CloudLab cluster",
    portal.ParameterType.STRING,
    "auto",
    [("auto", "Auto (from node type)"), ("utah", "Utah"), ("wisc", "Wisconsin"),
     ("clemson", "Clemson")],
    longDescription="Auto picks the cluster that has the chosen node type.",
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
    "writable_fs",
    "Build Junction with a writable host filesystem (needed for Postgres)",
    portal.ParameterType.BOOLEAN,
    True,
)
pc.defineParameter(
    "link_bw_kbps",
    "Link bandwidth (Kbps, 0 = default)",
    portal.ParameterType.INTEGER,
    0,
    advanced=True,
)

params = pc.bindParameters()

# ---- Check that node types and cluster agree ----
cluster = params.cluster
types = [("node0_type", params.node0_type)]
if params.num_nodes == 2:
    types.append(("node1_type", params.node1_type))
known = [(name, t, NODE_CLUSTER[t]) for name, t in types if t in NODE_CLUSTER]

if cluster == "auto":
    sites = set(site for _, _, site in known)
    if len(known) < len(types):
        pc.reportError(portal.ParameterError(
            "Unknown node type; pick the cluster by hand instead of Auto.",
            ["cluster"]))
    elif len(sites) > 1:
        pc.reportError(portal.ParameterError(
            "node0 and node1 types are on different clusters (%s); "
            "pick types from one cluster." % ", ".join(sorted(sites)),
            ["node0_type", "node1_type"]))
    else:
        cluster = sites.pop()
else:
    for name, t, site in known:
        if site != cluster:
            pc.reportError(portal.ParameterError(
                "%s is only on the %s cluster, not %s." % (t, site, cluster),
                [name, "cluster"]))

pc.verifyParameters()

IMAGE = "urn:publicid:IDN+%s.cloudlab.us+image+emulab-ops:UBUNTU24-64-STD" % cluster
CM = "urn:publicid:IDN+%s.cloudlab.us+authority+cm" % cluster

SETUP_COMMON = r"""
set -x
exec > >(sudo tee -a /local/setup.log) 2>&1
sudo mkdir -p /local && sudo chmod 1777 /local
cd /local
rm -f /local/setup.done /local/setup.failed
[ -d junction ] || git clone --branch {branch} {repo} junction
sudo chmod -R a+rwX /local/junction
cd /local/junction

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential \
  libreadline-dev zlib1g-dev bison flex libicu-dev pkg-config
{extra}
# Rust (nightly, per Junction README) in a shared location, not one user's home.
export RUSTUP_HOME=/local/rust/rustup CARGO_HOME=/local/rust/cargo
export PATH=$CARGO_HOME/bin:$PATH
if [ ! -x $CARGO_HOME/bin/rustc ]; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y --no-modify-path --default-toolchain=nightly
fi
sudo chmod -R a+rwX /local/rust
printf "export RUSTUP_HOME=/local/rust/rustup CARGO_HOME=/local/rust/cargo\nexport PATH=/local/rust/cargo/bin:\$PATH\n" \
  | sudo tee /etc/profile.d/rust.sh

if ./scripts/install.sh && {build}; then
  touch /local/setup.done
else
  touch /local/setup.failed
fi
sudo chmod -R a+rwX /local/junction /local/rust
"""

WORKLOAD_EXTRAS = r"""
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  iperf3 wrk memcached libmemcached-tools redis-tools netperf \
  linux-tools-common linux-tools-generic htop
"""


def setup_cmd(extra=""):
    if params.build_junction:
        build = "./scripts/build.sh" + (" -w" if params.writable_fs else "")
    else:
        build = "true"
    body = SETUP_COMMON.format(
        branch=params.junction_branch,
        repo=params.junction_repo,
        build=build,
        extra=extra,
    )
    # Run detached so the profile instantiates immediately.
    return "nohup bash -c '%s' >/dev/null 2>&1 &" % body.replace("'", "'\\''")


def add_workload_node(iface0):
    """node1 plus a direct link to node0's data0 interface."""
    node1 = request.RawPC("node1")
    node1.hardware_type = params.node1_type
    node1.component_manager_id = CM
    node1.disk_image = IMAGE
    iface1 = node1.addInterface("data0")
    iface1.addAddress(rspec.IPv4Address("10.10.1.2", "255.255.255.0"))
    node1.addService(rspec.Execute(shell="bash", command=setup_cmd(WORKLOAD_EXTRAS)))
    link = request.Link("data-link")
    link.addInterface(iface0)
    link.addInterface(iface1)
    if params.link_bw_kbps:
        link.bandwidth = params.link_bw_kbps


two_nodes = params.num_nodes == 2

# ---- Node 0: Junction + Postgres node ----
node0 = request.RawPC("node0")
node0.hardware_type = params.node0_type
node0.component_manager_id = CM
node0.disk_image = IMAGE
# With one node, node0 also carries the load-generator tools.
node0.addService(rspec.Execute(
    shell="bash", command=setup_cmd("" if two_nodes else WORKLOAD_EXTRAS)))

if two_nodes:
    iface0 = node0.addInterface("data0")
    iface0.addAddress(rspec.IPv4Address("10.10.1.1", "255.255.255.0"))
    add_workload_node(iface0)

pc.printRequestRSpec(request)

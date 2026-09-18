"""Build the `__config__` fixtures `tests/test_entrypoint.sh` reads.

The fixtures are **committed bytes**, not generated at test time: the test needs no
Python, no protobuf library and no nodo checkout, which is what makes it runnable in the
image as well as on a workstation. This script is here so that the bytes can be rebuilt
and so that what is in them is reviewable, not because the test runs it.

It writes what a node writes: `src/utils/configuration_file.write_config` serializes a
`celaut.ConfigurationFile`, and the peer instances inside it are exactly the shape
`src/manager/pow_networks.resolve_pow_network` builds -- one Instance per endpoint, one
`uri_slot` per instance, `Instance.Uri{ip, port}` inside it.

Run from a nodo checkout:

    cd /path/to/nodo
    python -c "import sys; sys.path.insert(0,'.'); \\
               exec(open('/path/to/ergo-node/tests/fixtures/make_fixtures.py').read())"

or with this file's directory as the working directory and nodo importable:

    PYTHONPATH=/path/to/nodo python tests/fixtures/make_fixtures.py
"""
import os
import sys

from protos import celaut_pb2 as celaut

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()


def instance(*addresses, internal=1):
    """One `celaut.Instance` at the given `(ip, port)` addresses.

    Several addresses in one instance means "one peer reachable at several addresses" --
    what `resolve_domain` builds out of a name's A records. `resolve_pow_network` never
    does that (separate operators are separate instances), but the entrypoint has to read
    both, so one fixture exercises it.
    """
    return celaut.Instance(
        api=celaut.Service.Api(slot=[celaut.Service.Api.Slot(
            port=internal,
            transport=celaut.Service.Api.Protocol(tags=["tcp"]),
            protocol_stack=[celaut.Service.Api.Protocol(tags=["ergo-p2p"])],
        )]),
        uri_slot=[celaut.Instance.Uri_Slot(
            internal_port=internal,
            uri=[celaut.Instance.Uri(ip=ip, port=port) for ip, port in addresses],
        )],
    )


def config(*resolutions):
    cf = celaut.ConfigurationFile()
    # Every `__config__` carries the node's own gateway, and it contains `uri` blocks of
    # exactly the shape a peer's has. It is in every fixture on purpose: a reader that
    # matched on shape rather than on the enclosing `network_resolution` would dial it.
    cf.gateway.CopyFrom(instance(("10.0.0.1", 8080)))
    for tags, peers in resolutions:
        cf.network_resolution.append(
            celaut.ConfigurationFile.NetworkResolution(tags=tags, peer_instances=peers)
        )
    cf.initial_sysresources.mem_limit = 2500000000
    return cf.SerializeToString()


FIXTURES = {
    # The ordinary case: our peers, plus another network's, plus the gateway.
    "config-three-peers": config(
        (["dns:example.com"], [instance(("93.184.216.34", 443))]),
        (["pow:ergo"], [
            instance(("213.239.193.208", 9030)),
            instance(("159.65.11.55", 9030)),
            # Bracketed by the entrypoint if the resolver did not: Ergo's own
            # mainnet.conf writes IPv6 knownPeers entries this way.
            instance(("2001:41d0:700:6662::1", 29031)),
        ]),
    ),
    # "Nobody meets that requirement right now" -- transient, and not an error.
    "config-no-peers": config((["pow:ergo"], [])),
    # This instance was resolved onto something, but not onto our chain.
    "config-other-network": config(
        (["dns:example.com"], [instance(("93.184.216.34", 443))]),
    ),
    # Two sources naming the same node. `knownPeers` is a set in meaning if not in type.
    "config-duplicate-peers": config((["pow:ergo"], [
        instance(("213.239.193.208", 9030)),
        instance(("159.65.11.55", 9030)),
        instance(("213.239.193.208", 9030)),
    ])),
    # A tag that merely starts with ours. `pow:ergo-testnet` is a different domain, and
    # a prefix match would put testnet peers in a mainnet node's knownPeers.
    "config-similar-tag": config(
        (["pow:ergo-testnet"], [instance(("192.0.2.1", 9023))]),
        (["pow:ergo"], [instance(("198.51.100.7", 9030))]),
    ),
    # One peer at two addresses.
    "config-multi-uri": config((["pow:ergo"], [
        instance(("203.0.113.5", 9030), ("203.0.113.6", 9030)),
    ])),
}

if __name__ == "__main__":
    target = HERE if len(sys.argv) < 2 else sys.argv[1]
    for name, payload in FIXTURES.items():
        path = os.path.join(target, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        print(f"{name}: {len(payload)} bytes")

# ergo-node

An Ergo reference node that finds its peers through the network it was *resolved* onto,
packaged as a Celaut service so a [nodo](https://github.com/celaut-project/nodo) can run
one itself.

## Why this exists

nodo already needs an Ergo node. It reads reputation proofs off the chain, it verifies
deposits, it signs payments — and all of that goes through `ledgers.ergo.NODE_URL`,
which today points at somebody else's node. That is one host deciding what this node
believes about the ledger it is paid in, and it is a URL in a config file rather than
anything the node verified.

So: the node runs the node. It hands this service an API key, the service comes up on
`:9053` speaking Ergo's ordinary REST API, and `ledgers.ergo.NODE_URL` points at
something the operator runs.

The interesting part is not the jar — it is **how it finds peers**.

## The `pow:ergo` network

An Ergo node needs a list of peers to dial. Every other packaging of one solves that by
hardcoding a list: Ergo's own `mainnet.conf` ships thirteen addresses, and every Docker
image built on it inherits them. That works, and it means the node's first act is to
trust a list somebody wrote down in 2021.

This service declares a network instead. `.service/service.json`:

```json
{
    "tags": ["pow:ergo"],
    "formal": {
        "pow.chain": "ergo",
        "pow.block_id": "a7439dad316fc1f387871b9839660b3ad3ada657e6cb0a9a28d807b78346aba9",
        "pow.min_cumulative_difficulty": "2749641828372274216960",
        "pow.min_height": "1870000",
        "pow.max_tip_age_s": "3600"
    }
}
```

That is not a list of hosts. It is a **property**: *a peer whose main chain contains this
block, behind which at least this much cumulative work has been done, whose tip is not
more than an hour stale.* The node running this instance is what turns the property into
addresses — it verifies candidates against exactly those conditions
(`src/manager/pow_networks.py`) and writes the survivors into this instance's
`__config__`, as the `ConfigurationFile.NetworkResolution` whose tags contain `pow:ergo`.

`service/entrypoint.sh` reads them out of there and writes every one into
`scorex.network.knownPeers` before starting the jar. **No address is hardcoded anywhere
in this repository.**

Three details of that declaration are deliberate:

**The block is a real one, at a round height.** Height 1870000, id
`a7439dad…46aba9`, fetched from a mainnet node:

```sh
curl -s https://node.sigmaspace.io/blocks/at/1870000
# ["a7439dad316fc1f387871b9839660b3ad3ada657e6cb0a9a28d807b78346aba9"]
```

**The work figure is the chain's score *at that block*, not at the current tip.** This is
the part worth reading twice. `/info` reports `fullBlocksScore` for whatever height the
peer is at now; putting *that* number in the spec would mean the ask silently got
stricter every time the file was written, and a spec whose meaning depends on when it was
authored is one nobody can read. So the figure here is the cumulative work up to height
1870000, derived by subtracting every block after it from a tip score:

```sh
# tip, at the time this was computed
curl -s https://node.sigmaspace.io/info
#   "fullHeight" : 1875916
#   "fullBlocksScore" : 2750033340952676925440
# every difficulty from 1870001 to 1875916, in 512-block slices:
curl -s "https://node.sigmaspace.io/blocks/chainSlice?fromHeight=H&toHeight=H2" | jq -r '.[].difficulty'
# sum = 391512580402708480  (5916 blocks, checked for gaps and duplicates)
# 2750033340952676925440 - 391512580402708480 = 2749641828372274216960
```

Which means the ask reads as *"a peer that has at least caught up to the block I named"*
and stays exactly as strict next year as it is today. `pow.min_height` restates the same
fact in a form the resolver can check from `/info` alone, before it spends two more
requests on the block itself.

**`pow.max_tip_age_s` is what keeps that from decaying.** A pinned block gets easier to
satisfy as the chain grows — every node passes it eventually. An hour's tip age is the
condition that still means something in 2030: a peer stalled an hour behind is no use for
bootstrapping, whatever it once verified.

**There is no `*` egress.** A Bitcoin node needs it, because it discovers peers through
DNS seeds and then dials whatever they hand back, so the set cannot be enumerated in
advance — [`celaut-basics/bitcoin-node`](https://github.com/celaut-basics/bitcoin-node)
says exactly that and asks for open egress. An Ergo node does not have to work that way:
given a peer list it starts from, `peerDiscovery` does the rest over the connections it
was granted. What this service asks to reach is a set the node verified, and the firewall
rule the node writes is for those peers and nothing else.

## The environment it reads

| variable | | what it is |
|---|---|---|
| `ERGO_API_KEY` | **required** | What callers authenticate to `:9053` with. Stored in `ergo.conf` as its BLAKE2b-256 hash, never in plaintext. |
| `ERGO_NETWORK` | `mainnet` | `mainnet` or `testnet`. |
| `ERGO_DATADIR` | `/data` | Where the node keeps its stores. |
| `ERGO_NODE_NAME` | `celaut-ergo-node` | The name sent in the P2P handshake. |
| `ERGO_MAX_HEAP` | `3G` | The JVM's `-Xmx`. Keep it under the instance's memory limit. |
| `ERGO_BLOCKS_TO_KEEP` | `1440` | Full blocks to retain — roughly a day. `-1` keeps all of them, turns the fast bootstrap off, and needs the disk raised accordingly. |
| `ERGO_WALLET_MNEMONIC` | — | Optional, and **requires `ERGO_BLOCKS_TO_KEEP=-1`** — see below. Restored through the node's own `/wallet/restore`. |
| `ERGO_WALLET_PASSWORD` | — | Required *if* a mnemonic is set: the keystore's encryption password. |
| `ERGO_WALLET_MNEMONIC_PASSPHRASE` | — | Optional BIP-39 passphrase. Unset and empty are **different wallets**. |

The REST API is on **9053 on both networks**, so whatever launches this has one endpoint
to talk to and does not have to know which chain it asked for.

## The wallet

Optional, and off unless `ERGO_WALLET_MNEMONIC` is set — a node that only needs to *read*
the chain should not set it, and then this service holds no key at all.

When it is set, the mnemonic goes to the node's own `/wallet/restore` rather than into a
keystore this service wrote. That is the whole design: Ergo owns that file's format and
its encryption parameters, the derivation is Ergo's own EIP-3 path (`m/44'/429'/0'/0/0`),
and **any standard Ergo wallet opens the same funds from the same words**, with no
knowledge of this service. A second implementation of the keystore in `bash` would be a
second thing to keep in step with a format this service does not define.

`usePre1627KeyDerivation` is sent as `false`. The pre-EIP-3 derivation was the default
before node 4.0.105, and restoring under it gives a *different* wallet, silently, for
anything generated since — a service with no way to know which era a mnemonic came from
should take the one every current tool produces.

### A wallet and a fast bootstrap cannot both be had

This is Ergo's rule, not this service's, and it is worth stating plainly because it
decides how the service is configured:

```scala
val isFullBlocksPruned: Boolean = blocksToKeep >= 0 || utxoSettings.utxoBootstrap

if (settings.nodeSettings.isFullBlocksPruned)
  Failure(new IllegalArgumentException("Unable to restore wallet when pruning is enabled"))
```
<sub>`NodeConfigurationSettings.scala`, `ErgoWalletService.scala`, v6.0.5</sub>

So the UTXO-snapshot bootstrap that makes this service fit in 8 GB is exactly what makes
`/wallet/restore` return HTTP 400. Setting `ERGO_WALLET_MNEMONIC` therefore **requires**
`ERGO_BLOCKS_TO_KEEP=-1`, which also turns the fast bootstrap off and means a full sync
from genesis on a disk much larger than the declared 8 GB.

The entrypoint refuses that combination at startup, with the rule quoted, rather than
letting the JVM start and the restore fail forty seconds later. A node that only *reads*
the chain — which is what `ledgers.ergo.NODE_URL` needs — should leave the mnemonic unset
and keep the cheap bootstrap.

(Both halves of `isFullBlocksPruned` have to move together, and so does a third setting:
Ergo also refuses to *start* with `nipopowBootstrap` on unless the node is pruned in one
of those two senses. That is why `utxoBootstrap` and `nipopowBootstrap` are one flag in
the entrypoint and not two — "bootstrap fast" and "keep everything" are the two
configurations Ergo actually has, and a mix of them is one it stops on. Both of these
were found by running the image, not by reading the docs.)

And the related limitation, for the unpruned case: **a snapshot-bootstrapped node cannot
rescan history it never downloaded.** With `utxoBootstrap = true` the node's state begins
at the snapshot, so even setting the rule above aside, a mnemonic with older history
would not show those funds.

## What it costs to run

The declared numbers, and where they come from:

**Disk: 8 GB.** The node boots from a verified UTXO set snapshot plus a NiPoPoW header
proof rather than replaying the chain, so the ~95% of history before the snapshot is
never downloaded. What is actually on disk is the UTXO set, the header chain, and
`ERGO_BLOCKS_TO_KEEP` full blocks:

- headers — `221` bytes each at every height sampled (100k, 500k, 1M, 1.5M, 1.875M, via
  `/blocks/{id}/header`), × ~1.88M heights ≈ **0.42 GB**;
- the UTXO snapshot — ergodocs' pruned-node page puts a bootstrap at "~1-2GB + recent
  blocks";
- 1440 full blocks at a mean of **30398 bytes** (measured over the last 4000 blocks via
  the explorer's `/api/v1/blocks`) ≈ **0.04 GB**.

That is ~2.5 GB of content, and 8 GB is the headroom RocksDB's compaction and the
snapshot download need on top of it. For scale: the same measurement puts the chain's
own growth at **~8 GB/year** (30398 B × 720 blocks/day × 365), which is what
`ERGO_BLOCKS_TO_KEEP=-1` would be signing up for and why it is not the default.

**Memory: 2 GB at init, 4 GB at most**, with `ERGO_MAX_HEAP=3G` inside it. Ergo's own
install documentation runs mainnet at `-Xmx4G` and notes that bootstrapping is the
memory-hungry phase; 3 GB of heap under a 4 GB instance limit leaves room for the JVM's
non-heap use and for RocksDB's off-heap caches, which are not in `-Xmx`.

**A Celaut instance has no persistent volume for its own data.** Stop the instance and
the stores go with it, so the next start bootstraps from a snapshot again — minutes
rather than the hours a full sync would take, which is most of the reason this service
bootstraps that way. Leave the instance running; the node only starts it when it is not
already up.

## Where the secret is

`ERGO_API_KEY` and, if set, `ERGO_WALLET_MNEMONIC` arrive in the environment. That means:

- they are in the node's `config.yaml`, and with a wallet configured that file is **the
  only backup of it** — this service stores no keys, it hands them to Ergo;
- the node records how each instance was launched and **redacts** these values, keeping
  the variable's name and not its contents;
- nothing here logs them. Not the mnemonic, not the API key, not the spending password.
  The API key reaches `curl` through a mode-0600 config file and the wallet request
  bodies through mode-0600 files built by `jq` from the environment — never through
  `argv`, which anything running as the same user can read out of `/proc`. The log says
  which network, which peers, what height it reached and the wallet's first address.

## Building it

```sh
nodo pack .        # produces the service and prints its id (content hash)
```

Then point the node at that id:

```yaml
core_services:
  ergo-node: "<the id nodo pack printed>"
```

The image is `linux/arm64`. The Ergo jar is JVM bytecode and is architecture-independent
— what `architecture` in `.service/service.json` describes is the base image and the JRE
tarball, so another architecture needs those two changed and nothing else.

Everything is pinned: the base image (`debian:bookworm-slim`) by digest, the Temurin 17
JRE by the SHA-256 Adoptium publishes, the Ergo 6.0.5 jar by a SHA-256 computed from the
published artifact, and the eight Debian packages on top to their exact versions.

Two of those deserve their caveat stated rather than glossed over. **Ergo publishes no
`SHA256SUMS` file**, so unlike Bitcoin Core's, the jar's checksum here has no upstream
document to be compared against: what is pinned is *an* artifact, in a reviewed file,
which cannot change without a diff. Verifying the release signature would be the
improvement. And **pinning packages to the patch version** means the build stops when one
of them leaves the mirror after a security update, until this file is edited — the same
trade `celaut-basics/bitcoin-node` already makes, and the one that keeps a key holder
from being "whatever the mirror served today".

## Tests

```sh
bash tests/test_entrypoint.sh
```

`bash`, `protoc`, `awk` and `b2sum` — the same four the service uses, which is what makes
the tests worth running on a workstation as well as in the image. On macOS:
`brew install protobuf coreutils`.

They cover the two things that fail *quietly* in production:

**Reading `__config__`.** Six committed fixtures, real serialized
`celaut.ConfigurationFile` messages built with nodo's own `celaut_pb2.py`
(`tests/fixtures/make_fixtures.py` rebuilds them, and is not run by the test — the bytes
are committed so the test needs no Python). They check that a `pow:ergo` resolution's
peers are read in order; that another network's peers and **the gateway instance** are
not, both of which contain `uri { ip, port }` blocks of exactly the same shape; that
`pow:ergo-testnet` is not matched as a prefix; that duplicates collapse; that every `uri`
of a multi-address instance is read; and that a missing `__config__` starts the node
rather than stopping it.

**The rendered config.** That an empty resolution writes `knownPeers = []` and not a
missing key — Ergo's `mainnet.conf` is a *fallback* under the user's config, so an absent
`knownPeers` is not "no peers", it is those thirteen hardcoded addresses, on a service
whose spec says it dials only what it was resolved onto. Also that the API key itself
appears nowhere in `ergo.conf`, and that the file and the curl config are mode 0600.

**The API key hash**, against the vector Ergo ships in its own `application.conf`,
`mainnet.conf` and `testnet.conf` (`blake2b-256("hello")` =
`324dcf02…1b72cf`, which the node's `/utils/hash/blake2b` returns for the same input),
and against BLAKE2b's published empty-input vector — which is there to pin the *output
length*, since a 256-bit BLAKE2b is a different hash and not a truncated 512-bit one.

**The pruning rule**, which is the one that decides how this service can be configured at
all: that a mnemonic on a pruned node is refused before the JVM starts, that
`ERGO_BLOCKS_TO_KEEP=-1` is accepted, and that it turns *both* bootstrap settings off.

And one regression that was found by writing them: `read_pow_peers` logs to **stderr**,
because its stdout is the peer list. A log line on the same stream would have been parsed
as an address.

What they do **not** cover is anything past that boundary: no JVM is started, no chain is
synced, no wallet is restored. That was done by hand instead — see below.

## What was verified by running it

The image was built for `linux/arm64` and run, which is where three of the bugs above
came from. In order:

- **The peer list reaches the node.** With a fixture `__config__` naming three `pow:ergo`
  peers, `/peers/all` came back with **exactly those three** and nothing else — which is
  the proof that `mainnet.conf`'s thirteen hardcoded addresses were not used — and
  `/peers/connected` showed a live handshake with one of them.
- **An empty resolution starts anyway**, logging that it did, with `knownPeers = []`.
- **SIGTERM shuts it down cleanly**: `docker stop` returned in 1.1 s with Ergo's own
  "Going to shutdown all connections & unbind port" and "Stopping ErgoNodeViewHolder" in
  the log, and PID 1 exited 143.
- **The wallet round-trips.** On `ERGO_BLOCKS_TO_KEEP=-1`, restore → unlock → an address
  read back out of `/wallet/addresses`. Restarting the container took the
  already-initialized path and unlocked the same wallet.
- **Nothing leaks.** `docker logs | grep -c` for the mnemonic, the API key and the
  spending password: **0** on every run.

What is still **not verified**: a full mainnet sync (the runs above reached height 0 —
they confirm the node starts, configures, peers and serves, not that it completes a UTXO
bootstrap), and `nodo pack .`, which needs a nodo with #383 and was not run.

## What this depends on

Two things in nodo, one merged-pending and one in flight. Neither is a limitation of this
service; both are named so the failure mode is recognizable.

- **[nodo#383](https://github.com/celaut-project/nodo/pull/383)** — the packer carries
  `network[].formal` and `network[].protocol_stack` from `service.json`. Before it,
  `parseNetwork` read `tags` and `prose` and dropped the rest, so the `formal` block
  above would pack to nothing and this service would ask for "any `pow:ergo` peer"
  instead of the one it declares. **`nodo pack .` against a tree without that PR produces
  a spec with an empty `formal`.**
- **[nodo#384](https://github.com/celaut-project/nodo/pull/384)** — the `pow:ergo`
  resolver emits each peer's P2P endpoint. A peer is *verified* over its REST API
  (`:9053`) and has to be *dialled* over Ergo's P2P port (`:9030` on mainnet, `:9023` on
  testnet), and before that PR `resolve_pow_network` built each `Instance.Uri` from the
  URL it verified. Without it, the addresses reaching `knownPeers` carry the REST port,
  and the node will not complete a handshake with them.
  The entrypoint writes whatever it is given — the port comes from the resolution, and
  translating it here would be this service second-guessing the resolver.

## What is not here

- **Mining.** `mining = false`, always. A validating node is what nodo needs.
- **`extraIndex`.** The indexed `/blockchain/...` routes cost disk and indexing time, and
  nothing in nodo's Ergo path reads them.
- **An archival node.** `ERGO_BLOCKS_TO_KEEP=-1` gets close, with the disk raised to
  match; the declared 8 GB does not fit the full chain.
- **Signature verification of the Ergo release** (above).
- **Tor.** Ergo's defaults, on the egress the node gives the instance.

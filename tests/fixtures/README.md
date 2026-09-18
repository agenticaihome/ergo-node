# `__config__` fixtures

Real serialized `celaut.ConfigurationFile` messages — the same bytes a node writes into
an instance's filesystem (`src/utils/configuration_file.write_config`).

They are **committed as bytes**, on purpose. `tests/test_entrypoint.sh` then needs only
`bash`, `protoc`, `awk` and `b2sum`, which is exactly what the image has, so the test runs
unchanged on a workstation and inside the container. Generating them at test time would
mean a Python and a protobuf library in both places, and a nodo checkout besides.

| fixture | what it is for |
|---|---|
| `config-three-peers` | The ordinary case: a `pow:ergo` resolution with three peers, another network's resolution, and the gateway — the last two containing `uri { ip, port }` blocks of exactly the shape a peer's has. |
| `config-no-peers` | A `pow:ergo` resolution that resolved to nobody. Not an error. |
| `config-other-network` | Resolved onto something, but not onto our chain. |
| `config-duplicate-peers` | The same address from two sources. |
| `config-similar-tag` | `pow:ergo-testnet` alongside `pow:ergo`, so a prefix match would be caught. |
| `config-multi-uri` | One instance at two addresses — what `resolve_domain` builds from a name's A records. |

## Rebuilding them

`make_fixtures.py` is the source of truth for what is in them. It is **not run by the
test**; it is here so the bytes are reviewable and reproducible.

```sh
PYTHONPATH=/path/to/nodo python tests/fixtures/make_fixtures.py
```

It imports `protos.celaut_pb2` from a nodo checkout rather than vendoring a generated
module: the fixtures have to be what that schema produces, and pinning them to a copy
would let the two drift without anything saying so.

## Reading one by hand

```sh
protoc --proto_path=service --decode=celaut.ConfigurationFile \
       service/celaut.proto < tests/fixtures/config-three-peers
```

That is the same command `service/entrypoint.sh` runs, with the same vendored schema.

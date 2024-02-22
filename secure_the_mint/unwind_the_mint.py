from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Coroutine, Dict, List, Optional

import click
from blspy import G2Element
from chia.cmds.cmds_util import get_wallet
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.types.announcement import Announcement
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend, compute_additions_with_cost
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import decode_puzzle_hash
from chia.util.config import load_config
from chia.util.ints import uint64
from chia.wallet.util.tx_config import DEFAULT_TX_CONFIG

from secure_the_bag import (
    TargetCoin,
    batch_the_bag,
    parent_of_puzzle_hash,
    read_secure_the_bag_targets,
    secure_the_bag,
)

NULL_SIGNATURE = G2Element()


async def unspent_coin_exists(
    full_node_client: FullNodeRpcClient, coin_name: bytes32
) -> bool:
    """
    Checks if an unspent coin exists.

    Raises an exception if coin has already been spent.
    """
    coin_record = await full_node_client.get_coin_record_by_name(coin_name)

    if coin_record is None:
        return False

    if coin_record.spent_block_index > 0:
        raise Exception("Coin {} has already been spent".format(coin_name))

    return True


async def wait_for_unspent_coin(
    full_node_client: FullNodeRpcClient, coin_name: bytes32
) -> None:
    """
    Repeatedly poll full node until unspent coin is created.

    Raises an exception if coin has already been spent.
    """
    while True:
        print(f"Waiting for unspent coin {coin_name.hex()}")

        exists = await unspent_coin_exists(full_node_client, coin_name)

        if exists:
            print(f"Coin {coin_name.hex()} exists and is unspent")

            break

        print(f"Unspent coin {coin_name.hex()} does not exist")

        await asyncio.sleep(3)


async def wait_for_coin_spend(
    full_node_client: FullNodeRpcClient, coin_name: bytes32
) -> None:
    """
    Repeatedly poll full node until coin is spent.

    This is used to wait for coins spend before spending children.
    """
    while True:
        print(f"Waiting for coin spend {coin_name.hex()}")

        coin_record = await full_node_client.get_coin_record_by_name(coin_name)

        if coin_record is None:
            print(f"Coin {coin_name.hex()} does not exist")

            continue

        if coin_record.spent_block_index > 0:
            print(f"Coin {coin_name.hex()} has been spent")

            break

        print(f"Coin {coin_name.hex()} has not been spent")

        await asyncio.sleep(3)


async def get_unwind(
    full_node_client: FullNodeRpcClient,
    genesis_coin_id: bytes32,
    parent_puzzle_lookup: Dict[str, TargetCoin],
    target_puzzle_hash: bytes32,
) -> List[CoinSpend]:
    required_coin_spends: List[CoinSpend] = []

    current_puzzle_hash = target_puzzle_hash

    while True:
        if current_puzzle_hash is None:
            break

        coin_spend, _ = parent_of_puzzle_hash(
            genesis_coin_id, current_puzzle_hash, parent_puzzle_lookup
        )

        if coin_spend is None:
            break

        response = await full_node_client.get_coin_record_by_name(
            coin_spend.coin.name()
        )

        if response is None:
            # Coin doesn't exist yet so we add to list of required spends and check the parent
            required_coin_spends.append(coin_spend)
            current_puzzle_hash = coin_spend.coin.puzzle_hash
            continue

        if response.spent_block_index == 0:
            # We have reached the lowest unspent coin
            required_coin_spends.append(coin_spend)
        else:
            # This situation is only expected if the bag has already been unwound (possibly by somebody else)
            print("WARNING: Lowest coin is spent. Secured bag already unwound.")

        break

    return required_coin_spends


async def unwind_the_bag(
    full_node_client: FullNodeRpcClient,
    unwind_target_puzzle_hash_bytes: bytes32,
    genesis_coin_id: bytes32,
    parent_puzzle_lookup: Dict[str, TargetCoin],
) -> List[CoinSpend]:
    current_puzzle_hash = unwind_target_puzzle_hash_bytes

    print(f"Getting unwind for {current_puzzle_hash}")

    required_coin_spends: List[CoinSpend] = await get_unwind(
        full_node_client,
        genesis_coin_id,
        parent_puzzle_lookup,
        current_puzzle_hash,
    )

    print(
        f"{len(required_coin_spends)} spends required to unwind the bag to {unwind_target_puzzle_hash_bytes}"
    )

    return required_coin_spends[::-1]


async def app(
    chia_config: Dict[str, Any],
    chia_root: Path,
    metadata: str,
    leaf_width: int,
    unwind_target_puzzle_hash_bytes: Optional[bytes32],
    genesis_coin_id: bytes32,
    fingerprint: int,
    wallet_id: int,
    unwind_fee: int,
) -> None:
    full_node_client = await FullNodeRpcClient.create(
        chia_config["self_hostname"],
        chia_config["full_node"]["rpc_port"],
        chia_root,
        load_config(chia_root, "config.yaml"),
    )
    wallet_client = await WalletRpcClient.create(
        chia_config["self_hostname"],
        chia_config["wallet"]["rpc_port"],
        chia_root,
        load_config(chia_root, "config.yaml"),
    )
    if fingerprint is not None:
        print("Setting fingerprint: {}".format(fingerprint))
        await wallet_client.log_in(fingerprint)

    targets, mint_spends = read_secure_the_bag_targets(metadata)
    _, parent_puzzle_lookup = secure_the_bag(targets, leaf_width)

    if unwind_target_puzzle_hash_bytes is not None:
        # Unwinding to a single target has to be done sequentially as each spend is dependant on the parent being spent
        print(f"Unwinding secured bag to {unwind_target_puzzle_hash_bytes}")

        coin_spends = await unwind_the_bag(
            full_node_client,
            unwind_target_puzzle_hash_bytes,
            genesis_coin_id,
            parent_puzzle_lookup,
        )

        for coin_spend in coin_spends:
            await get_wallet(
                root_path=chia_root,
                wallet_client=wallet_client,
                fingerprint=fingerprint,
            )

            additions, cost = compute_additions_with_cost(coin_spend)
            addition_amount = sum([c.amount for c in additions])
            missing_amount = addition_amount - coin_spend.coin.amount

            if unwind_fee > 0:
                fee_coins = await wallet_client.select_coins(
                    amount=unwind_fee + missing_amount,
                    wallet_id=wallet_id,
                    coin_selection_config=DEFAULT_TX_CONFIG.coin_selection_config,
                )
                change_amount = (
                    sum([c.amount for c in fee_coins]) - unwind_fee - missing_amount
                )
                change_address = await wallet_client.get_next_address(
                    wallet_id=wallet_id, new_address=False
                )
                change_ph = decode_puzzle_hash(change_address)

                # Fees depend on announcements made by secure the bag coins to ensure they can't be seperated
                coin_announcements: List[Announcement] = [
                    Announcement(coin_spend.coin.name(), b"$")
                ]

                # Create signed coin spends and change for fees
                fees_tx = await wallet_client.create_signed_transaction(
                    [{"amount": change_amount, "puzzle_hash": change_ph}],
                    coins=fee_coins,
                    fee=uint64(unwind_fee),
                    coin_announcements=coin_announcements,
                    tx_config=DEFAULT_TX_CONFIG,
                )

                if fees_tx.spend_bundle is None:
                    raise Exception("No spend bundle created")

                bundle = SpendBundle(
                    [coin_spend] + fees_tx.spend_bundle.coin_spends,
                    fees_tx.spend_bundle.aggregated_signature,
                )
                await full_node_client.push_tx(bundle)  # type: ignore[no-untyped-call]
            else:
                await wallet_client.push_tx(
                    SpendBundle([coin_spend], G2Element())
                )  # type: ignore[no-untyped-call]

            print("Transaction pushed to full node")

            # Wait for parent coin to be spent before attempting to spend children
            await wait_for_coin_spend(full_node_client, coin_spend.coin.name())

        coin_spend, _ = parent_of_puzzle_hash(
            genesis_coin_id, unwind_target_puzzle_hash_bytes, parent_puzzle_lookup
        )
        spends = mint_spends[unwind_target_puzzle_hash_bytes].to_coin_spends(
            coin_spend.coin.name()
        )
        response = await full_node_client.get_coin_record_by_name(spends[0].coin.name())
        if response.spent_block_index == 0:
            mint_spend_bundle = SpendBundle(spends, G2Element())
            # TODO add fees
            await full_node_client.push_tx(mint_spend_bundle)
        else:
            print(f"{coin_spend.coin.name().hex()} already minted")
    else:
        # Unwinding the entire secured bag can involve batching spends together for speed
        # Care must be taken to only batch together spends where the parent has been spent
        # otherwise one invalid spend could invalidate the entire spend bundle
        print(f"Unwinding entire secured bag with {len(targets)} NFTs")

        batched_targets = batch_the_bag(targets, leaf_width)

        # Dictionary of spends at each level of the tree so they can be batched
        # based on parents that have already been spent
        level_coin_spends: Dict[int, Dict[str, CoinSpend]] = defaultdict(dict)
        max_depth = 0
        total_spends = 0

        # Unwind to the first target coin in each batch
        for batch_targets in batched_targets:
            unwound_spends = await unwind_the_bag(
                full_node_client,
                batch_targets[0].puzzle_hash,
                genesis_coin_id,
                parent_puzzle_lookup,
            )
            total_spends += len(unwound_spends)

            print(f"{len(unwound_spends)} spends to {batch_targets[0].puzzle_hash}")

            for index, coin_spend in enumerate(unwound_spends):
                level_coin_spends[index][coin_spend.coin.puzzle_hash.hex()] = coin_spend
                if index > max_depth:
                    max_depth = index

        total_fees = total_spends * unwind_fee

        print(f"{total_spends} total spends required with {total_fees} fees")

        for depth in range(0, max_depth + 1):
            level = level_coin_spends[depth]

            # Larger batch_size e.g. 25 can result in COST_EXCEEDS_MAX
            batch_size = 10
            spent_coin_names: List[bytes32] = []
            bundle_spends: List[CoinSpend] = []

            print(f"About to iterate {len(level.values())} times for depth {depth}")

            i = 0
            for coin_spend in level.values():
                i += 1

                await get_wallet(
                    root_path=chia_root,
                    wallet_client=wallet_client,
                    fingerprint=fingerprint,
                )
                additions, cost = compute_additions_with_cost(coin_spend)
                addition_amount = sum([c.amount for c in additions])
                missing_amount = addition_amount - coin_spend.coin.amount

                bundle_spends.append(coin_spend)
                spent_coin_names.append(coin_spend.coin.name())

                if len(bundle_spends) >= batch_size or i == len(level.values()):
                    if unwind_fee > 0:
                        spend_bundle_fee = len(bundle_spends) * unwind_fee

                        fee_coins = await wallet_client.select_coins(
                            amount=spend_bundle_fee + missing_amount,
                            wallet_id=wallet_id,
                            coin_selection_config=DEFAULT_TX_CONFIG.coin_selection_config,
                        )
                        change_amount = (
                            sum([c.amount for c in fee_coins])
                            - spend_bundle_fee
                            - missing_amount
                        )
                        change_address = await wallet_client.get_next_address(
                            wallet_id=wallet_id, new_address=False
                        )
                        change_ph = decode_puzzle_hash(change_address)

                        # Fees depend on announcements made by secure the bag coins to ensure they can't be seperated
                        coin_announcements = []
                        for coin_spend in bundle_spends:
                            coin_announcements.append(
                                Announcement(coin_spend.coin.name(), b"$")
                            )

                        # Create signed coin spends and change for fees
                        fees_tx = await wallet_client.create_signed_transaction(
                            [{"amount": change_amount, "puzzle_hash": change_ph}],
                            coins=fee_coins,
                            fee=uint64(spend_bundle_fee),
                            coin_announcements=coin_announcements,
                            tx_config=DEFAULT_TX_CONFIG,
                        )
                        if fees_tx.spend_bundle is None:
                            raise Exception("No spend bundle created")

                        await full_node_client.push_tx(
                            SpendBundle(
                                bundle_spends + fees_tx.spend_bundle.coin_spends,
                                fees_tx.spend_bundle.aggregated_signature,
                            )  # type: ignore[no-untyped-call]
                        )
                    else:
                        await full_node_client.push_tx(
                            SpendBundle(bundle_spends, G2Element())
                        )  # type: ignore[no-untyped-call]

                    print(
                        f"Transaction containing {len(bundle_spends)} coin spends "
                        f"at tree depth {depth} pushed to full node"
                    )

                    bundle_spends = []

                    # Wait for this batch to be spent before attempting next spends
                    # Important for spending children of coins we just created
                    coin_spend_waits: List[Coroutine[Any, Any, None]] = []

                    for coin_name in spent_coin_names:
                        coin_spend_waits.append(
                            wait_for_coin_spend(full_node_client, coin_name)
                        )

                    await asyncio.gather(*coin_spend_waits)

                    spent_coin_names = []

        # Offer creation
        for mint_target in targets[0:3]:
            leaf_puzzle_hash = mint_target.puzzle_hash
            coin_spend, _ = parent_of_puzzle_hash(
                genesis_coin_id, leaf_puzzle_hash, parent_puzzle_lookup
            )
            nft_mint_spends = mint_spends[leaf_puzzle_hash]
            offer = nft_mint_spends.to_offer(
                coin_spend.coin.name()
            )
            print(offer.to_bech32())
            print("------")


        # Direct minting
        # batched_mints = batch_the_bag(targets, 25)
        # for mint_batch in batched_mints:
        #     coin_spends = []
        #     for mint_target in mint_batch:
        #         leaf_puzzle_hash = mint_target.puzzle_hash
        #         coin_spend, _ = parent_of_puzzle_hash(
        #             genesis_coin_id, leaf_puzzle_hash, parent_puzzle_lookup
        #         )
        #         spends = mint_spends[leaf_puzzle_hash].to_coin_spends(
        #             coin_spend.coin.name()
        #         )
        #         leaf_name = spends[0].coin.name()
        #         response = await full_node_client.get_coin_record_by_name(leaf_name)
        #         if response.spent_block_index == 0:
        #             coin_spends += spends
        #     if len(coin_spends) > 0:
        #         print(f"Minting batch with {len(coin_spends)/3} NFTs")
        #         mint_spend_bundle = SpendBundle(coin_spends, G2Element())
        #         await full_node_client.push_tx(mint_spend_bundle)
        #         await wait_for_coin_spend(
        #             full_node_client, mint_spend_bundle.coin_spends[0].coin.name()
        #         )

    full_node_client.close()
    wallet_client.close()
    await full_node_client.await_closed()
    await wallet_client.await_closed()


@click.command()
@click.pass_context
@click.option(
    "-dcid",
    "--did-coin-id",
    required=True,
    help="ID of coin that was spent to create secured bag",
)
@click.option(
    "-m",
    "--metadata",
    required=True,
    help="Path to CSV file containing the NFT metadata",
)
@click.option(
    "-utph",
    "--unwind-target-puzzle-hash",
    required=False,
    help="Puzzle hash of target to unwind from secured bag",
)
@click.option(
    "-wi",
    "--wallet-id",
    type=int,
    help="The wallet id to use",
)
@click.option(
    "-f",
    "--fingerprint",
    type=int,
    default=None,
    help="The wallet fingerprint to use as funds",
)
@click.option(
    "-uf",
    "--unwind-fee",
    required=True,
    default=500000,
    show_default=True,
    help="Fee paid for each unwind spend. Enough mojos must be available to cover all spends.",
)
@click.option(
    "-lw",
    "--leaf-width",
    required=True,
    default=100,
    show_default=True,
    help="Secure the bag leaf width",
)
def cli(
    ctx: click.Context,
    did_coin_id: str,
    metadata: str,
    unwind_target_puzzle_hash: str,
    fingerprint: int,
    wallet_id: int,
    unwind_fee: int,
    leaf_width: int,
) -> None:
    ctx.ensure_object(dict)

    did_coin_id_bytes = bytes32.fromhex(did_coin_id)
    unwind_target_puzzle_hash_bytes = None
    if unwind_target_puzzle_hash:
        unwind_target_puzzle_hash_bytes = bytes32.fromhex(unwind_target_puzzle_hash)

    chia_root: Path = Path(
        os.path.expanduser(os.getenv("CHIA_ROOT", "~/.chia/mainnet"))
    ).resolve()
    chia_config = load_config(chia_root, "config.yaml")

    asyncio.get_event_loop().run_until_complete(
        app(
            chia_config,
            chia_root,
            metadata,
            leaf_width,
            unwind_target_puzzle_hash_bytes,
            did_coin_id_bytes,
            fingerprint,
            wallet_id,
            unwind_fee,
        )
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

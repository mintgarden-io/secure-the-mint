from __future__ import annotations

import csv
from typing import Any, Dict, List, Tuple, Union, Optional, TypeVar

import click
from blspy import G2Element
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import encode_puzzle_hash, decode_puzzle_hash
from chia.util.byte_types import hexstr_to_bytes
from chia.util.ints import uint64, uint16
from chia.wallet.nft_wallet import nft_puzzles
from chia.wallet.nft_wallet.nft_puzzles import (
    SINGLETON_MOD_HASH,
    NFT_STATE_LAYER_MOD_HASH,
    NFT_METADATA_UPDATER,
    NFT_OWNERSHIP_LAYER_HASH,
    NFT_TRANSFER_PROGRAM_DEFAULT,
    create_ownership_layer_puzzle,
    LAUNCHER_PUZZLE_HASH,
)
from chia.wallet.outer_puzzles import match_puzzle
from chia.wallet.payment import Payment
from chia.wallet.puzzle_drivers import PuzzleInfo
from chia.wallet.puzzles.load_clvm import load_clvm_maybe_recompile
from chia.wallet.singleton import (
    SINGLETON_LAUNCHER_PUZZLE_HASH,
    SINGLETON_LAUNCHER_PUZZLE,
)
from chia.wallet.trading.offer import OFFER_MOD_HASH, NotarizedPayment, Offer
from chia.wallet.uncurried_puzzle import uncurry_puzzle

# Fees spend asserts this. Message not required as inner puzzle contains hardcoded coin spends
# and doesn't accept a solution.
EMPTY_COIN_ANNOUNCEMENT = [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"]

PRE_LAUNCHER_MOD = load_clvm_maybe_recompile(
    "secure_the_mint_launcher.clsp",
    package_or_requirement="secure_the_mint.puzzles",
    recompile=True,
)
SECURE_P2_DELEGATE = load_clvm_maybe_recompile(
    "secure_the_mint_p2_delegated_puzzle.clsp",
    package_or_requirement="secure_the_mint.puzzles",
    recompile=True,
)
DIRECT_DELEGATE = load_clvm_maybe_recompile(
    "secure_the_mint_direct_delegate.clsp",
    package_or_requirement="secure_the_mint.puzzles",
    recompile=True,
)
OFFER_DELEGATE = load_clvm_maybe_recompile(
    "secure_the_mint_offer_delegate.clsp",
    package_or_requirement="secure_the_mint.puzzles",
    recompile=True,
)


class Target:
    puzzle_hash: bytes32
    amount: uint64

    def __init__(self, puzzle_hash: bytes32, amount: uint64) -> None:
        self.puzzle_hash = puzzle_hash
        self.amount = amount

    def create_coin_condition(self) -> List[Any]:
        return [
            ConditionOpcode.CREATE_COIN,
            self.puzzle_hash,
            self.amount,
            [self.puzzle_hash],
        ]


class TargetCoin:
    target: Target
    puzzle: Program
    puzzle_hash: bytes32
    amount: uint64

    def __init__(self, target: Target, puzzle: Program, amount: uint64) -> None:
        self.target = target
        self.puzzle = puzzle
        self.puzzle_hash = puzzle.get_tree_hash()
        self.amount = amount


class MintSpends:
    pre_launcher_puzzle: Program
    eve_p2_puzzle: Program
    metadata: Program
    royalty_percentage: uint16
    royalty_puzzle_hash: bytes32
    requested_payments: Optional[Dict[Optional[bytes32], List[Payment]]]

    def __init__(
        self,
        pre_launcher_puzzle: Program,
        eve_p2_puzzle: Program,
        metadata: Program,
        royalty_percentage: uint16,
        royalty_puzzle_hash: bytes32,
        requested_payments: Dict[Optional[bytes32], List[Payment]] = None,
    ) -> None:
        self.pre_launcher_puzzle = pre_launcher_puzzle
        self.eve_p2_puzzle = eve_p2_puzzle
        self.metadata = metadata
        self.royalty_percentage = royalty_percentage
        self.royalty_puzzle_hash = royalty_puzzle_hash
        self.requested_payments = requested_payments

    def get_nft_puzzle(self, launcher_coin: Coin, p2_puzzle: Program) -> Program:
        return nft_puzzles.create_full_puzzle(
            launcher_coin.name(),
            self.metadata,
            NFT_METADATA_UPDATER.get_tree_hash(),
            create_ownership_layer_puzzle(
                launcher_coin.name(),
                b"",
                p2_puzzle,
                self.royalty_percentage,
                royalty_puzzle_hash=self.royalty_puzzle_hash,
            ),
        )

    def to_coin_spends(self, pre_launcher_parent_id: bytes32) -> List[CoinSpend]:
        amount = uint64(1)
        pre_launcher_coin = Coin(
            pre_launcher_parent_id, self.pre_launcher_puzzle.get_tree_hash(), amount
        )
        mode = 1  # 1 for mint, 0 for melt
        pre_launcher_solution = Program.to([mode, pre_launcher_coin.name()])
        pre_launcher_spend = CoinSpend(
            pre_launcher_coin,
            self.pre_launcher_puzzle,
            pre_launcher_solution,
        )
        launcher_coin = Coin(
            pre_launcher_coin.name(), SINGLETON_LAUNCHER_PUZZLE_HASH, amount
        )
        eve_puzzle = self.get_nft_puzzle(launcher_coin, self.eve_p2_puzzle)

        launcher_solution = Program.to([eve_puzzle.get_tree_hash(), amount, []])
        launcher_spend = CoinSpend(
            launcher_coin, SINGLETON_LAUNCHER_PUZZLE, launcher_solution
        )

        eve_coin = Coin(launcher_coin.name(), eve_puzzle.get_tree_hash(), amount)
        innersol = Program.to([pre_launcher_coin.name(), [eve_coin.name()]])
        ownership_layer_solution = Program.to([innersol])  # supports DID
        nft_layer_solution = Program.to([ownership_layer_solution])
        singleton_solution = Program.to(
            [
                [launcher_coin.parent_coin_info, uint64(launcher_coin.amount)],
                amount,
                nft_layer_solution,
            ]
        )

        eve_spend = CoinSpend(eve_coin, eve_puzzle, singleton_solution)

        return [pre_launcher_spend, launcher_spend, eve_spend]

    def to_offer(
        self,
        pre_launcher_parent_id: bytes32,
    ) -> Offer:
        if self.requested_payments is None:
            raise Exception("This target does not request a payment")

        coin_spends = self.to_coin_spends(pre_launcher_parent_id)

        launcher_coin = coin_spends[1].coin
        eve_coin = coin_spends[2].coin
        notarized_payments: Dict[Optional[bytes32], List[NotarizedPayment]] = {}
        for asset_id, payments in self.requested_payments.items():
            assert not asset_id  # Only XCH payments for now
            notarized_payments[asset_id] = []
            for p in payments:
                puzzle_hash, amount, memos = tuple(p.as_condition_args())
                notarized_payments[asset_id].append(
                    NotarizedPayment(puzzle_hash, amount, memos, eve_coin.name())
                )

        bundle = SpendBundle(coin_spends, G2Element())
        puzzle_info: Optional[PuzzleInfo] = match_puzzle(
            uncurry_puzzle(coin_spends[2].puzzle_reveal)
        )
        offer = Offer(notarized_payments, bundle, {launcher_coin.name(): puzzle_info})
        return offer


T = TypeVar("T")


def batch_the_bag(targets: List[T], leaf_width: int) -> List[List[T]]:
    """
    Batches the bag by leaf width.
    """
    results = []
    current_batch = []

    for index, target in enumerate(targets):
        current_batch.append(target)

        if len(current_batch) == leaf_width or index == len(targets) - 1:
            results.append(current_batch)
            current_batch = []

    return results


def secure_the_bag(
    targets: List[Target],
    leaf_width: int,
    parent_puzzle_lookup: Dict[str, TargetCoin] = {},
) -> Tuple[bytes32, Dict[str, TargetCoin]]:
    """
    Calculates secure the bag root puzzle hash and provides parent puzzle reveal lookup table for spending.
    """

    if len(targets) == 1:
        return targets[0].puzzle_hash, parent_puzzle_lookup

    results: List[Target] = []

    batched_targets = batch_the_bag(targets, leaf_width)
    batch_count = len(batched_targets)

    print(f"Batched the bag into {batch_count} batches")

    processed = 0

    for batch_targets in batched_targets:
        print(
            f"{round((processed / batch_count) * 100, 2)}% of the way through batches"
        )

        list_of_conditions = [EMPTY_COIN_ANNOUNCEMENT]
        total_amount = 0

        print(f"Creating coin with {len(batch_targets)} targets")

        for target in batch_targets:
            list_of_conditions.append(target.create_coin_condition())
            total_amount += target.amount

        puzzle = Program.to((1, list_of_conditions))
        puzzle_hash = puzzle.get_tree_hash()
        amount = total_amount

        results.append(Target(puzzle_hash, uint64(amount)))

        for target in batch_targets:
            parent_puzzle_lookup[target.puzzle_hash.hex()] = TargetCoin(
                target, puzzle, uint64(amount)
            )

        processed += 1

    return secure_the_bag(results, leaf_width, parent_puzzle_lookup)


def parent_of_puzzle_hash(
    genesis_coin_name: bytes32,
    puzzle_hash: bytes32,
    parent_puzzle_lookup: Dict[str, TargetCoin],
) -> Tuple[Union[CoinSpend, None], bytes32]:
    parent: Union[TargetCoin, None] = parent_puzzle_lookup.get(puzzle_hash.hex())

    if parent is None:
        return None, genesis_coin_name

    # We need the parent of the parent in order to calculate the coin name
    _, parent_coin_info = parent_of_puzzle_hash(
        genesis_coin_name, parent.puzzle_hash, parent_puzzle_lookup
    )

    coin = Coin(
        parent_coin_info,
        parent.puzzle_hash,
        0 if parent_coin_info == genesis_coin_name else parent.amount,
    )

    return CoinSpend(coin, parent.puzzle, Program.to([])), coin.name()


def read_secure_the_bag_targets(
    metadata_path: str,
    target_puzzle_hash: bytes32,
    royalty_puzzle_hash: bytes32,
    royalty_percentage_times_100: uint16,
    melt_public_key: Optional[bytes32] = None,
    requested_mojos: Optional[uint64] = None,
) -> Tuple[List[Target], Dict[bytes32, MintSpends]]:
    targets: List[Target] = []
    mint_spends: Dict[bytes32, MintSpends] = {}

    metadata_list, _ = read_metadata_csv(metadata_path, has_header=True)
    for meta in metadata_list:
        if "uris" not in meta.keys():
            return {"success": False, "error": "Data URIs is required"}
        if not isinstance(meta["uris"], list):
            return {"success": False, "error": "Data URIs must be a list"}
        if not isinstance(meta.get("meta_uris", []), list):
            return {"success": False, "error": "Metadata URIs must be a list"}
        if not isinstance(meta.get("license_uris", []), list):
            return {"success": False, "error": "License URIs must be a list"}
        nft_metadata = [
            ("u", meta["uris"]),
            ("h", hexstr_to_bytes(meta["hash"])),
            ("mu", meta.get("meta_uris", [])),
            ("lu", meta.get("license_uris", [])),
            ("sn", uint64(meta.get("edition_number", 1))),
            ("st", uint64(meta.get("edition_total", 1))),
        ]
        if "meta_hash" in meta and len(meta["meta_hash"]) > 0:
            nft_metadata.append(("mh", hexstr_to_bytes(meta["meta_hash"])))
        if "license_hash" in meta and len(meta["license_hash"]) > 0:
            nft_metadata.append(("lh", hexstr_to_bytes(meta["license_hash"])))
        metadata_program = Program.to(nft_metadata)

        if requested_mojos is not None:
            requested_payments = {
                None: [Payment(target_puzzle_hash, requested_mojos, [])]
                if requested_mojos > 0
                else []
            }
            payments = Program.to(
                [p.as_condition_args() for p in requested_payments[None]]
            )
            trade_prices = Program.to(
                [[p.amount, OFFER_MOD_HASH] for p in requested_payments[None]]
            )
            eve_delegated_puzzle = OFFER_DELEGATE.curry(
                OFFER_MOD_HASH, payments, trade_prices
            )
        else:
            requested_payments = None
            eve_delegated_puzzle = DIRECT_DELEGATE.curry(target_puzzle_hash)

        p2_puzzle = SECURE_P2_DELEGATE.curry(eve_delegated_puzzle)
        pre_launcher_puzzle = PRE_LAUNCHER_MOD.curry(
            SINGLETON_MOD_HASH,
            LAUNCHER_PUZZLE_HASH,
            NFT_STATE_LAYER_MOD_HASH,
            metadata_program.get_tree_hash(),
            NFT_METADATA_UPDATER.get_tree_hash(),
            NFT_OWNERSHIP_LAYER_HASH,
            NFT_TRANSFER_PROGRAM_DEFAULT.get_tree_hash(),
            royalty_puzzle_hash,
            royalty_percentage_times_100,
            p2_puzzle.get_tree_hash(),
            melt_public_key
        )
        pre_launcher_target = Target(pre_launcher_puzzle.get_tree_hash(), uint64(1))
        targets.append(pre_launcher_target)
        mint_spends[pre_launcher_puzzle.get_tree_hash()] = MintSpends(
            pre_launcher_puzzle,
            p2_puzzle,
            metadata_program,
            royalty_percentage_times_100,
            royalty_puzzle_hash,
            requested_payments,
        )

    return targets, mint_spends


def read_metadata_csv(
    file_path: str,
    has_header: Optional[bool] = False,
    has_targets: Optional[bool] = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    with open(file_path, "r") as f:
        csv_reader = csv.reader(f)
        bulk_data = list(csv_reader)
    metadata_list: List[Dict[str, Any]] = []
    if has_header:
        header_row = bulk_data[0]
        rows = bulk_data[1:]
    else:
        header_row = [
            "hash",
            "uris",
            "meta_hash",
            "meta_uris",
            "license_hash",
            "license_uris",
            "edition_number",
            "edition_total",
        ]
        if has_targets:
            header_row.append("target")
        rows = bulk_data
    list_headers = ["uris", "meta_uris", "license_uris"]
    targets = []
    for row in rows:
        meta_dict: Dict[str, Any] = {
            list_headers[i]: [] for i in range(len(list_headers))
        }
        for i, header in enumerate(header_row):
            if header in list_headers:
                meta_dict[header].append(row[i])
            elif header == "target":
                targets.append(row[i])
            else:
                meta_dict[header] = row[i]
        metadata_list.append(meta_dict)
    return metadata_list, targets


@click.command()
@click.pass_context
@click.option(
    "-m",
    "--metadata",
    required=True,
    help="Path to CSV file containing the NFT metadata",
)
@click.option(
    "-lw",
    "--leaf-width",
    required=True,
    default=25,
    show_default=True,
    help="Secure the bag leaf width",
)
@click.option(
    "-pr",
    "--prefix",
    required=True,
    default="xch",
    show_default=True,
    help="Address prefix",
)
@click.option(
    "-ta",
    "--target-address",
    required=True,
    default="xch",
    show_default=True,
    help="Address to receive the NFT or the offer payment",
)
@click.option(
    "-rm",
    "--requested-mojos",
    required=False,
    help="Amount of mojos to request as payment when minting an NFT",
)
def cli(
    ctx: click.Context,
    metadata: str,
    leaf_width: int,
    prefix: str,
    target_address: str,
    requested_mojos: Optional[int] = None,
) -> None:
    ctx.ensure_object(dict)

    target_puzzle_hash = decode_puzzle_hash(target_address)

    targets, mint_spends = read_secure_the_bag_targets(
        metadata,
        target_puzzle_hash,
        target_puzzle_hash,
        uint16(5 * 100),
        requested_mojos=requested_mojos
    )
    root_puzzle_hash, parent_puzzle_lookup = secure_the_bag(targets, leaf_width)

    print(f"Secure the bag root amount: {len(targets)} mojos")

    print(f"Secure the bag root puzzle hash: {root_puzzle_hash}")

    # parent = parent_puzzle_lookup.get(targets[0].puzzle_hash.hex())
    # while True:
    #     print(parent.puzzle_hash.hex())
    #     new_parent = parent_puzzle_lookup.get(parent.puzzle_hash.hex())
    #     if new_parent:
    #         parent = new_parent
    #     else:
    #         print("Root puzzle", parent.puzzle)
    #         break


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

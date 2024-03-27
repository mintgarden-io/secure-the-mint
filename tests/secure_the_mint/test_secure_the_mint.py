from __future__ import annotations

from typing import Optional

import pytest
from chia.types.blockchain_format.program import Program, INFINITE_COST
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.condition_opcodes import ConditionOpcode
from chia.util.hash import std_hash
from chia.util.ints import uint64, uint16
from chia.wallet.nft_wallet.uncurry_nft import UncurriedNFT
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_LAUNCHER_HASH
from chia.wallet.trading.offer import OFFER_MOD_HASH
from clvm.casts import int_to_bytes
from clvm_tools.binutils import disassemble

from secure_the_mint.secure_the_mint import (
    Target,
    batch_the_bag,
    parent_of_puzzle_hash,
    read_secure_the_bag_targets,
    secure_the_bag,
)


def test_batch_the_bag() -> None:
    targets = [
        Target(
            bytes32.fromhex(
                "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
            ),
            uint64(10000000000000000),
        ),
        Target(
            bytes32.fromhex(
                "f3d5162330c4d6c8b9a0aba5eed999178dd2bf466a7a0289739acc8209122e2c"
            ),
            uint64(32100000000),
        ),
        Target(
            bytes32.fromhex(
                "7ffdeca4f997bde55d249b4a3adb8077782bc4134109698e95b10ea306a138b4"
            ),
            uint64(10000000000000000),
        ),
    ]
    results = batch_the_bag(targets, 2)

    assert len(results) == 2
    assert len(results[0]) == 2
    assert len(results[1]) == 1

    assert results[0][0].puzzle_hash == bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    assert results[0][0].amount == uint64(10000000000000000)

    assert results[0][1].puzzle_hash == bytes32.fromhex(
        "f3d5162330c4d6c8b9a0aba5eed999178dd2bf466a7a0289739acc8209122e2c"
    )
    assert results[0][1].amount == uint64(32100000000)

    assert results[1][0].puzzle_hash == bytes32.fromhex(
        "7ffdeca4f997bde55d249b4a3adb8077782bc4134109698e95b10ea306a138b4"
    )
    assert results[1][0].amount == uint64(10000000000000000)


def test_secure_the_bag() -> None:
    target_1_puzzle_hash = bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    target_1_amount = uint64(10000000000000000)
    target_2_puzzle_hash = bytes32.fromhex(
        "f3d5162330c4d6c8b9a0aba5eed999178dd2bf466a7a0289739acc8209122e2c"
    )
    target_2_amount = uint64(32100000000)
    target_3_puzzle_hash = bytes32.fromhex(
        "7ffdeca4f997bde55d249b4a3adb8077782bc4134109698e95b10ea306a138b4"
    )
    target_3_amount = uint64(10000000000000000)

    targets = [
        Target(target_1_puzzle_hash, target_1_amount),
        Target(target_2_puzzle_hash, target_2_amount),
        Target(target_3_puzzle_hash, target_3_amount),
    ]
    root_hash, parent_puzzle_lookup = secure_the_bag(targets, 2)

    # Calculates correct root hash
    assert (
        root_hash.hex()
        == "2a21783e7b1f5ab453e45315a35c1e02c4dd7234f3f41d2d64541819431d049d"
    )

    node_1_puzzle_hash = bytes32.fromhex(
        "f2cff3b95ddbaa61a214220d67a20901c584ff16df12ec769844f391d513835c"
    )
    node_2_puzzle_hash = bytes32.fromhex(
        "f45579725598a28c5572d8c534be3edf095830de0f984f0eb3d9bb251c71134b"
    )

    root_puzzle = Program.to(
        (
            1,
            [
                [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
                [
                    ConditionOpcode.CREATE_COIN,
                    node_1_puzzle_hash,
                    uint64(10000032100000000),
                    [node_1_puzzle_hash],
                ],
                [
                    ConditionOpcode.CREATE_COIN,
                    node_2_puzzle_hash,
                    uint64(10000000000000000),
                    [node_2_puzzle_hash],
                ],
            ],
        )
    )

    # Puzzle reveal for root hash is correct
    assert root_puzzle.get_tree_hash().hex() == root_hash.hex()

    r = root_puzzle.run(0)

    expected_result = Program.to(
        [
            [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
            [
                ConditionOpcode.CREATE_COIN,
                node_1_puzzle_hash,
                uint64(10000032100000000),
                [node_1_puzzle_hash],
            ],
            [
                ConditionOpcode.CREATE_COIN,
                node_2_puzzle_hash,
                uint64(10000000000000000),
                [node_2_puzzle_hash],
            ],
        ]
    )

    # Result of running root is correct
    assert r.get_tree_hash().hex() == expected_result.get_tree_hash().hex()

    node_1_puzzle = Program.to(
        (
            1,
            [
                [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
                [
                    ConditionOpcode.CREATE_COIN,
                    target_1_puzzle_hash,
                    target_1_amount,
                    [target_1_puzzle_hash],
                ],
                [
                    ConditionOpcode.CREATE_COIN,
                    target_2_puzzle_hash,
                    target_2_amount,
                    [target_2_puzzle_hash],
                ],
            ],
        )
    )

    # Puzzle reveal for node 1 is correct
    assert node_1_puzzle.get_tree_hash().hex() == node_1_puzzle_hash.hex()

    r = node_1_puzzle.run(0)

    expected_result = Program.to(
        [
            [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
            [
                ConditionOpcode.CREATE_COIN,
                target_1_puzzle_hash,
                target_1_amount,
                [target_1_puzzle_hash],
            ],
            [
                ConditionOpcode.CREATE_COIN,
                target_2_puzzle_hash,
                target_2_amount,
                [target_2_puzzle_hash],
            ],
        ]
    )

    # Result of running node 1 is correct
    assert r.get_tree_hash().hex() == expected_result.get_tree_hash().hex()

    node_2_puzzle = Program.to(
        (
            1,
            [
                [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
                [
                    ConditionOpcode.CREATE_COIN,
                    target_3_puzzle_hash,
                    target_3_amount,
                    [target_3_puzzle_hash],
                ],
            ],
        )
    )

    # Puzzle reveal for node 2 is correct
    assert node_2_puzzle.get_tree_hash().hex() == node_2_puzzle_hash.hex()

    r = node_2_puzzle.run(0)

    expected_result = Program.to(
        [
            [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
            [
                ConditionOpcode.CREATE_COIN,
                target_3_puzzle_hash,
                target_3_amount,
                [target_3_puzzle_hash],
            ],
        ]
    )

    # Result of running node 2 is correct
    assert r.get_tree_hash().hex() == expected_result.get_tree_hash().hex()

    # Parent puzzle lookup (used for puzzle reveals)

    puzzle_create_target_1 = parent_puzzle_lookup.get(target_1_puzzle_hash.hex())
    puzzle_create_target_2 = parent_puzzle_lookup.get(target_2_puzzle_hash.hex())
    puzzle_create_target_3 = parent_puzzle_lookup.get(target_3_puzzle_hash.hex())

    assert puzzle_create_target_1 is not None
    assert puzzle_create_target_2 is not None
    assert puzzle_create_target_3 is not None

    # Targets 1 & 2 are created by spending node 1
    assert (
        puzzle_create_target_1.puzzle.get_tree_hash().hex() == node_1_puzzle_hash.hex()
    )
    assert (
        puzzle_create_target_2.puzzle.get_tree_hash().hex() == node_1_puzzle_hash.hex()
    )

    # Target 3 is created by spending node 2
    assert (
        puzzle_create_target_3.puzzle.get_tree_hash().hex() == node_2_puzzle_hash.hex()
    )

    puzzle_create_node_1 = parent_puzzle_lookup.get(node_1_puzzle_hash.hex())
    puzzle_create_node_2 = parent_puzzle_lookup.get(node_2_puzzle_hash.hex())

    assert puzzle_create_node_1 is not None
    assert puzzle_create_node_2 is not None

    # Nodes 1 & 2 are created by spending root
    assert puzzle_create_node_1.puzzle.get_tree_hash().hex() == root_hash.hex()
    assert puzzle_create_node_2.puzzle.get_tree_hash().hex() == root_hash.hex()


def test_parent_of_puzzle_hash() -> None:
    target_1_puzzle_hash = bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    target_1_amount = uint64(1)
    target_2_puzzle_hash = bytes32.fromhex(
        "f3d5162330c4d6c8b9a0aba5eed999178dd2bf466a7a0289739acc8209122e2c"
    )
    target_2_amount = uint64(1)
    target_3_puzzle_hash = bytes32.fromhex(
        "7ffdeca4f997bde55d249b4a3adb8077782bc4134109698e95b10ea306a138b4"
    )
    target_3_amount = uint64(1)

    targets = [
        Target(target_1_puzzle_hash, target_1_amount),
        Target(target_2_puzzle_hash, target_2_amount),
        Target(target_3_puzzle_hash, target_3_amount),
    ]
    _, parent_puzzle_lookup = secure_the_bag(targets, 2)

    genesis_coin_name = bytes32.fromhex(
        "2676b64fab1f562cc4788cb2a9dbbe31da09da9cc23118dfccf6ad741d652328"
    )
    expected_node_1_coin_name = bytes32.fromhex(
        "d214e605e13ff6d10393b0862078ec201f594f6decc0077d0f3175395027c8ed"
    )
    expected_root_coin_name = bytes32.fromhex(
        "84caacbab76c0e741940fe597fe2385e5670f13240466aed0a0bb12aef7621ba"
    )

    coin_spend, coin_name = parent_of_puzzle_hash(
        genesis_coin_name, target_1_puzzle_hash, parent_puzzle_lookup
    )

    # Coin name of node 1
    assert coin_spend is not None
    assert coin_spend.coin.name() == expected_node_1_coin_name
    assert coin_name == expected_node_1_coin_name

    pp = parent_puzzle_lookup.get(target_1_puzzle_hash.hex())
    assert pp is not None
    node_1_puzzle_hash = pp.puzzle_hash

    coin_spend, coin_name = parent_of_puzzle_hash(
        genesis_coin_name, node_1_puzzle_hash, parent_puzzle_lookup
    )

    # Coin name of root
    assert coin_spend is not None
    assert coin_spend.coin.name() == expected_root_coin_name
    assert coin_name == expected_root_coin_name
    pp = parent_puzzle_lookup.get(node_1_puzzle_hash.hex())
    assert pp is not None
    root_puzzle_hash = pp.puzzle_hash

    coin_spend, puzzle_hash = parent_of_puzzle_hash(
        genesis_coin_name, root_puzzle_hash, parent_puzzle_lookup
    )

    # Genesis
    assert coin_spend is None
    assert puzzle_hash == bytes32.fromhex(
        "2676b64fab1f562cc4788cb2a9dbbe31da09da9cc23118dfccf6ad741d652328"
    )

    # Confirm expected root coin name is correct
    root_coin_name = std_hash(
        genesis_coin_name
        + root_puzzle_hash
        + int_to_bytes(0)  # root coin has amount 0 so it can be created from a DID
    )

    assert root_coin_name == expected_root_coin_name

    node_1_puzzle = Program.to(
        (
            1,
            [
                [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, b"$"],
                [
                    ConditionOpcode.CREATE_COIN,
                    target_1_puzzle_hash,
                    target_1_amount,
                    [target_1_puzzle_hash],
                ],
                [
                    ConditionOpcode.CREATE_COIN,
                    target_2_puzzle_hash,
                    target_2_amount,
                    [target_2_puzzle_hash],
                ],
            ],
        )
    )

    # Confirm expected node 1 coin name is correct
    node_1_coin_name = std_hash(
        root_coin_name
        + node_1_puzzle.get_tree_hash()
        + int_to_bytes(target_1_amount + target_2_amount)
    )

    assert node_1_coin_name == expected_node_1_coin_name


@pytest.mark.parametrize(
    "requested_mojos",
    [10000, None],
)
def test_read_secure_the_bag_targets(requested_mojos: Optional[int]) -> None:
    target_puzzle_hash = bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    melt_public_key = bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    targets, mint_spends = read_secure_the_bag_targets(
        "./tests/secure_the_mint/metadata.csv",
        target_puzzle_hash,
        target_puzzle_hash,
        uint16(5 * 100),
        melt_public_key,
        requested_mojos,
    )

    assert len(targets) == 3
    assert len(mint_spends) == 3

    pre_launcher_parent_id = bytes32.fromhex(
        "f3153d27c1d14581971203f10082fa2db2fbc0fd786a9b210e43f227eca499b5"
    )

    mint_spend_0 = mint_spends.get(targets[0].puzzle_hash)
    coin_spends_0 = mint_spend_0.to_coin_spends(pre_launcher_parent_id)
    pre_launcher_spend = coin_spends_0[0]
    assert pre_launcher_spend.coin.parent_coin_info == pre_launcher_parent_id
    assert pre_launcher_spend.coin.amount == 1
    if requested_mojos:
        assert bytes32(pre_launcher_spend.coin.puzzle_hash) == bytes32.fromhex(
            "455ce2a6ea837ba124548e574475430edfce0a9add8a087fd7a6a1e593950b58"
        )
    else:
        assert bytes32(pre_launcher_spend.coin.puzzle_hash) == bytes32.fromhex(
            "36d16c1fee484220fb22dc45c1ebed3195ee577dcfdb61dd98f99579146cb4cf"
        )
    #
    # print(
    #     SpendBundle(
    #         coin_spends=coin_spends_0, aggregated_signature=G2Element()
    #     ).to_json_dict()
    # )

    assert targets[0].puzzle_hash == pre_launcher_spend.coin.puzzle_hash
    assert targets[0].amount == pre_launcher_spend.coin.amount

    launcher_spend = coin_spends_0[1]
    assert launcher_spend.coin.parent_coin_info == pre_launcher_spend.coin.name()
    assert launcher_spend.coin.amount == 1
    assert bytes32(launcher_spend.coin.puzzle_hash) == SINGLETON_LAUNCHER_HASH

    eve_spend = coin_spends_0[2]
    assert eve_spend.coin.parent_coin_info == launcher_spend.coin.name()
    assert eve_spend.coin.amount == 1

    _, pre_launcher_conditions = pre_launcher_spend.puzzle_reveal.run_with_cost(
        INFINITE_COST, pre_launcher_spend.solution
    )
    assert pre_launcher_conditions.first().first() == ConditionOpcode.ASSERT_MY_COIN_ID

    # pre launcher asserts launcher coin announcement
    assert (
        pre_launcher_conditions.rest().rest().first().first()
        == ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT
    )
    announcement_message = Program.to(
        [eve_spend.coin.puzzle_hash, 1, []]
    ).get_tree_hash()
    assert pre_launcher_conditions.rest().rest().first().rest().first() == std_hash(
        launcher_spend.coin.name() + announcement_message
    )

    _, eve_spend_conditions = eve_spend.puzzle_reveal.run_with_cost(
        INFINITE_COST, eve_spend.solution
    )

    if requested_mojos:
        offer = mint_spend_0.to_offer(pre_launcher_parent_id)
        assert len(offer.requested_payments[None]) == 1
        assert offer.requested_payments[None][0].amount == requested_mojos
        assert offer.requested_payments[None][0].puzzle_hash == target_puzzle_hash

        # eve coin asserts offer condition
        assert_puzzle_condition = eve_spend_conditions.rest().rest().rest().first()

        assert (
            assert_puzzle_condition.first()
            == ConditionOpcode.ASSERT_PUZZLE_ANNOUNCEMENT
        )

        msg: bytes32 = Program.to(
            (
                offer.requested_payments[None][0].nonce,
                [p.as_condition_args() for p in offer.requested_payments[None]],
            )
        ).get_tree_hash()
        assert assert_puzzle_condition.rest().first() == std_hash(OFFER_MOD_HASH + msg)


def test_dynamic_read_secure_the_bag_targets() -> None:
    requested_mojos = uint64(100000)

    target_puzzle_hash = bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    melt_public_key = bytes32.fromhex(
        "4bc6435b409bcbabe53870dae0f03755f6aabb4594c5915ec983acf12a5d1fba"
    )
    targets, mint_spends = read_secure_the_bag_targets(
        "./tests/secure_the_mint/metadata.csv",
        target_puzzle_hash,
        target_puzzle_hash,
        uint16(5 * 100),
        melt_public_key,
        requested_mojos,
        allow_update_on_mint=True,
    )

    updated_targets, updated_mint_spends = read_secure_the_bag_targets(
        "./tests/secure_the_mint/metadata_updated.csv",
        target_puzzle_hash,
        target_puzzle_hash,
        uint16(5 * 100),
        melt_public_key,
        requested_mojos,
        allow_update_on_mint=True,
    )

    assert len(targets) == 3
    assert len(mint_spends) == 3

    pre_launcher_parent_id = bytes32.fromhex(
        "f3153d27c1d14581971203f10082fa2db2fbc0fd786a9b210e43f227eca499b5"
    )

    mint_spend_0 = mint_spends.get(targets[0].puzzle_hash)
    updated_metadata = updated_mint_spends[updated_targets[0].puzzle_hash].metadata
    coin_spends_0 = mint_spend_0.to_coin_spends(pre_launcher_parent_id, updated_metadata)
    pre_launcher_spend = coin_spends_0[0]
    assert pre_launcher_spend.coin.parent_coin_info == pre_launcher_parent_id
    assert pre_launcher_spend.coin.amount == 1
    if requested_mojos:
        assert bytes32(pre_launcher_spend.coin.puzzle_hash) == bytes32.fromhex(
            "b8d65b74b86cb863dc97aed08f65a7bff8666614fd02b08053a6bebc88ff6c79"
        )
    else:
        assert bytes32(pre_launcher_spend.coin.puzzle_hash) == bytes32.fromhex(
            "36d16c1fee484220fb22dc45c1ebed3195ee577dcfdb61dd98f99579146cb4cf"
        )
    #
    # print(
    #     SpendBundle(
    #         coin_spends=coin_spends_0, aggregated_signature=G2Element()
    #     ).to_json_dict()
    # )

    assert targets[0].puzzle_hash == pre_launcher_spend.coin.puzzle_hash
    assert targets[0].amount == pre_launcher_spend.coin.amount

    launcher_spend = coin_spends_0[1]
    assert launcher_spend.coin.parent_coin_info == pre_launcher_spend.coin.name()
    assert launcher_spend.coin.amount == 1
    assert bytes32(launcher_spend.coin.puzzle_hash) == SINGLETON_LAUNCHER_HASH

    eve_spend = coin_spends_0[2]
    assert eve_spend.coin.parent_coin_info == launcher_spend.coin.name()
    assert eve_spend.coin.amount == 1

    uncurried_nft = UncurriedNFT.uncurry(*eve_spend.puzzle_reveal.uncurry())
    assert uncurried_nft.metadata == updated_mint_spends[updated_targets[0].puzzle_hash].metadata

    _, pre_launcher_conditions = pre_launcher_spend.puzzle_reveal.run_with_cost(
        INFINITE_COST, pre_launcher_spend.solution
    )
    assert pre_launcher_conditions.first().first() == ConditionOpcode.ASSERT_MY_COIN_ID

    # pre launcher asserts launcher coin announcement
    assert (
        pre_launcher_conditions.rest().rest().first().first()
        == ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT
    )
    announcement_message = Program.to(
        [eve_spend.coin.puzzle_hash, 1, []]
    ).get_tree_hash()
    assert pre_launcher_conditions.rest().rest().first().rest().first() == std_hash(
        launcher_spend.coin.name() + announcement_message
    )

    _, eve_spend_conditions = eve_spend.puzzle_reveal.run_with_cost(
        INFINITE_COST, eve_spend.solution
    )

    if requested_mojos:
        offer = mint_spend_0.to_offer(pre_launcher_parent_id, updated_metadata)
        assert len(offer.requested_payments[None]) == 1
        assert offer.requested_payments[None][0].amount == requested_mojos
        assert offer.requested_payments[None][0].puzzle_hash == target_puzzle_hash

        # eve coin asserts offer condition
        assert_puzzle_condition = eve_spend_conditions.rest().rest().rest().first()

        assert (
            assert_puzzle_condition.first()
            == ConditionOpcode.ASSERT_PUZZLE_ANNOUNCEMENT
        )

        msg: bytes32 = Program.to(
            (
                offer.requested_payments[None][0].nonce,
                [p.as_condition_args() for p in offer.requested_payments[None]],
            )
        ).get_tree_hash()
        assert assert_puzzle_condition.rest().first() == std_hash(OFFER_MOD_HASH + msg)

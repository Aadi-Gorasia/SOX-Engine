/**
 * ============================================================================
 *  uttt_bitboard.h  —  GameState, move generation, win detection  v4
 *
 *  CLOUD DEPLOYMENT: Shared header — no changes needed for cloud use.
 *  Built into uttt_engine binary on the VM alongside uttt_uci.cpp.
 * ============================================================================
 *
 *  INDEX LAYOUT  (macro-major, the ONLY layout that makes the engine correct)
 *  ──────────────────────────────────────────────────────────────────────────
 *  global index = macro * 9 + local_cell
 *
 *  This means indices 0-8 are ALL in macro 0 (top-left 3×3 block).
 *  Indices 9-17 are ALL in macro 1 (top-center 3×3 block). Etc.
 *
 *  How macro-major indices map to the 9×9 display grid:
 *    display_row = (macro / 3) * 3 + local_cell / 3
 *    display_col = (macro % 3) * 3 + local_cell % 3
 *
 *  Cartesian (x,y) from display position:
 *    x = display_col - 4        (left = -4, right = +4)
 *    y = -(display_row - 4)     (top = +4, bottom = -4)
 *
 *  Verified full grid (idx shown at each display position):
 *    Macro:  0  0  0 |  1  1  1 |  2  2  2
 *    Index:  0  1  2 |  9 10 11 | 18 19 20
 *            3  4  5 | 12 13 14 | 21 22 23
 *            6  7  8 | 15 16 17 | 24 25 26
 *           ----------+-----------+----------
 *    Macro:  3  3  3 |  4  4  4 |  5  5  5
 *    Index: 27 28 29 | 36 37 38 | 45 46 47
 *           30 31 32 | 39 40 41 | 48 49 50
 *           33 34 35 | 42 43 44 | 51 52 53
 *           ----------+-----------+----------
 *    Macro:  6  6  6 |  7  7  7 |  8  8  8
 *    Index: 54 55 56 | 63 64 65 | 72 73 74
 *           57 58 59 | 66 67 68 | 75 76 77
 *           60 61 62 | 69 70 71 | 78 79 80
 *
 *  Key consequences:
 *    • idx=40 → macro=4, cell=4 → display(4,4) → x=0, y=0  ✓ centre
 *    • idx=7  → macro=0, cell=7 → display(2,1) → x=-3,y=+2  (bottom-centre of top-left macro)
 *    • Sending to macro c after playing cell c is trivially correct: idx/9 == macro
 *
 *  LOCAL WIN MASKS (same for every macro, 9-bit):
 *    Cell layout within a macro:
 *      0 1 2
 *      3 4 5
 *      6 7 8
 *    Winning lines:
 *      Rows:  0x007(0-1-2)  0x038(3-4-5)  0x1C0(6-7-8)
 *      Cols:  0x049(0-3-6)  0x092(1-4-7)  0x124(2-5-8)
 *      Diag:  0x111(0-4-8)  0x054(2-4-6)
 * ============================================================================
 */

#pragma once
#ifndef UTTT_BITBOARD_H
#define UTTT_BITBOARD_H

#include "uttt_engine.h"
#include <cstring>
#include <random>

static constexpr u16 LOCAL_WIN_MASKS[8] = {
    0x007, 0x038, 0x1C0,
    0x049, 0x092, 0x124,
    0x111, 0x054
};

// Globals defined in uttt_main.cpp
extern u64 zobrist_table[TOTAL_SQUARES][2];
extern u64 zobrist_macro[MACRO_COUNT + 1];  // [0..9]; 9 = "any macro"
extern u64 zobrist_side_to_move;

// ─── GameState ────────────────────────────────────────────────────────────────
struct GameState {
    u128 bb[2];          // bb[0]=X bits, bb[1]=O bits (macro-major indexed)
    u16  macro_bb[2];    // 9-bit: which macros each player has won
    u16  macro_full;     // 9-bit: macros that are complete (won or drawn)
    u8   active_macro;   // 0-8: must play here; 9: free choice
    u8   side;           // 0=X, 1=O
    u8   moves_played;
    u8   game_over;      // 0=ongoing 1=X wins 2=O wins 3=draw
    u64  hash;
    Move history[TOTAL_SQUARES];

    void reset() {
        bb[0] = bb[1] = 0;
        macro_bb[0] = macro_bb[1] = 0;
        macro_full   = 0;
        active_macro = 9;
        side         = 0;
        moves_played = 0;
        game_over    = 0;
        memset(history, NO_MOVE, sizeof(history));
        // Hash encodes: active_macro key always present;
        // zobrist_side_to_move present iff O to move.
        hash = zobrist_macro[9]; // start: active=any, X to move
    }

    // 9-bit occupancy for player p in macro m
    inline u16 local_occ(int p, int m) const {
        return static_cast<u16>((bb[p] >> (m * 9)) & 0x1FF);
    }

    static inline bool local_has_win(u16 pat) {
        for (auto mask : LOCAL_WIN_MASKS)
            if ((pat & mask) == mask) return true;
        return false;
    }

    static inline int popcount9(u16 v) { return __builtin_popcount(v & 0x1FF); }

    inline int count_threats(u16 att, u16 blk) const {
        int t = 0;
        for (auto mask : LOCAL_WIN_MASKS) {
            if ((blk & mask) == 0 && popcount9(att & mask) == 2) ++t;
        }
        return t;
    }
};

// ─── Zobrist init ─────────────────────────────────────────────────────────────
inline void init_zobrist() {
    std::mt19937_64 rng(0xDEADBEEF'CAFEBABE);
    for (int sq = 0; sq < TOTAL_SQUARES; ++sq)
        for (int p = 0; p < 2; ++p)
            zobrist_table[sq][p] = rng();
    for (int m = 0; m <= MACRO_COUNT; ++m)
        zobrist_macro[m] = rng();
    zobrist_side_to_move = rng();
}

// ─── apply_move ───────────────────────────────────────────────────────────────
//
//  Hash invariant: hash = XOR of:
//    zobrist_table[sq][p] for every occupied square
//    zobrist_macro[active_macro]
//    zobrist_side_to_move  iff  side == O(1)
//
inline bool apply_move(GameState& gs, Move move) {
    const int m = move / 9;   // macro  (0-8)
    const int c = move % 9;   // cell   (0-8)
    const int p = gs.side;

    // Remove old hash components
    gs.hash ^= zobrist_macro[gs.active_macro];
    if (p == 1) gs.hash ^= zobrist_side_to_move;

    // Place piece
    gs.bb[p] |= (u128(1) << move);
    gs.hash  ^= zobrist_table[move][p];

    // Update macro state
    if (!((gs.macro_bb[0] | gs.macro_bb[1] | gs.macro_full) & (1 << m))) {
        u16 local = gs.local_occ(p, m);
        if (GameState::local_has_win(local)) {
            gs.macro_bb[p] |= (1 << m);
            gs.macro_full  |= (1 << m);
        } else if ((gs.local_occ(0,m) | gs.local_occ(1,m)) == 0x1FF) {
            gs.macro_full |= (1 << m);
        }
    }

    // Next active macro: the local cell index IS the target macro index
    gs.active_macro = (gs.macro_full & (1 << c)) ? 9u : static_cast<u8>(c);

    // Win detection
    if (GameState::local_has_win(gs.macro_bb[p]))
        gs.game_over = (p == 0) ? 1 : 2;
    else if (gs.macro_full == 0x1FF)
        gs.game_over = 3;

    gs.history[gs.moves_played++] = move;
    gs.side ^= 1;

    // Add new hash components
    gs.hash ^= zobrist_macro[gs.active_macro];
    if (gs.side == 1) gs.hash ^= zobrist_side_to_move;

    return true;
}

// ─── get_legal_moves ──────────────────────────────────────────────────────────
inline int get_legal_moves(const GameState& gs, Move* moves) {
    int count = 0;
    const u128 all_occ = gs.bb[0] | gs.bb[1];

    u16 macro_mask = (gs.active_macro < 9)
        ? static_cast<u16>(1 << gs.active_macro)
        : static_cast<u16>(~gs.macro_full & 0x1FF);

    u16 mm = macro_mask;
    while (mm) {
        int m  = __builtin_ctz(mm);
        mm    &= mm - 1;
        u32 occ   = static_cast<u32>((all_occ >> (m * 9)) & 0x1FF);
        u32 empty = (~occ) & 0x1FF;
        int base  = m * 9;
        while (empty) {
            int c  = __builtin_ctz(empty);
            empty &= empty - 1;
            moves[count++] = static_cast<Move>(base + c);
        }
    }
    return count;
}

// ─── Coordinate conversions ───────────────────────────────────────────────────
//
//  global idx (macro-major) ↔ display position (row, col) ↔ Cartesian (x, y)
//
//  display_row = (m / 3) * 3 + c / 3
//  display_col = (m % 3) * 3 + c % 3
//  x = display_col - 4
//  y = -(display_row - 4)

inline void idx_to_display(int idx, int& drow, int& dcol) {
    int m = idx / 9, c = idx % 9;
    drow = (m / 3) * 3 + c / 3;
    dcol = (m % 3) * 3 + c % 3;
}

inline void idx_to_xy(int idx, int& x, int& y) {
    int drow, dcol;
    idx_to_display(idx, drow, dcol);
    x =  dcol - 4;
    y = -(drow - 4);
}

// (x,y) → global index
inline int xy_to_index(int x, int y) {
    int dcol = x + 4;
    int drow = 4 - y;
    int m = (drow / 3) * 3 + dcol / 3;
    int c = (drow % 3) * 3 + dcol % 3;
    return m * 9 + c;
}

// Display position → global index
inline int display_to_idx(int drow, int dcol) {
    int m = (drow / 3) * 3 + dcol / 3;
    int c = (drow % 3) * 3 + dcol % 3;
    return m * 9 + c;
}

inline int macro_of(int idx) { return idx / 9; }
inline int cell_of (int idx) { return idx % 9; }

// Centre (x,y) of a macro board
inline void macro_center_xy(int m, int& cx, int& cy) {
    // Centre cell of macro m is local cell 4
    idx_to_xy(m * 9 + 4, cx, cy);
}

#endif // UTTT_BITBOARD_H
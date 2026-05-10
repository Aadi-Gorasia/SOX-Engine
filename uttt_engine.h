#pragma once
/**
 * ============================================================================
 *  ULTIMATE TIC-TAC-TOE ENGINE — GRANDMASTER LEVEL
 *  uttt_engine.h — Core types, constants, and declarations
 *
 *  CLOUD DEPLOYMENT: This header is shared between uttt_uci.cpp (the cloud
 *  binary, managed by uttt_server.py) and uttt_main.cpp (local interactive
 *  mode).  No changes needed here for cloud use.
 * ============================================================================
 *
 *  COORDINATE SYSTEM
 *  -----------------
 *  The visible board uses Cartesian coords X ∈ [-4,+4], Y ∈ [-4,+4].
 *  The dead center of the 9×9 grid is (0,0).
 *
 *  Mapping to a flat 0‥80 bit-index:
 *      col   = x + 4          (0‥8)
 *      row   = y + 4          (0‥8)   ← note: +Y goes UP in math; we store
 *                                        row 0 at the top so Y=+4 → row 0
 *      index = row * 9 + col
 *
 *  BOARD LAYOUT (bit-indices)
 *  --------------------------
 *  Macro-board r ∈ [0,8]:  r = macro_row * 3 + macro_col
 *  Cell c ∈ [0,8] within that macro-board: c = local_row * 3 + local_col
 *
 *  Global bit-index:  idx = r * 9 + c
 *
 *  BITBOARDS
 *  ---------
 *  We keep two 128-bit integers (unsigned __int128) — one for X and one for O.
 *  Bit `idx` is set if that player owns square `idx`.
 *
 *  Macro-level state is derived from these atomically with bitwise ops.
 * ============================================================================
 */

#ifndef UTTT_ENGINE_H
#define UTTT_ENGINE_H

#include <cstdint>
#include <array>
#include <vector>
#include <atomic>
#include <mutex>
#include <random>
#include <cmath>
#include <cassert>
#include <climits>

// ─── Convenience type aliases ────────────────────────────────────────────────
using u8   = uint8_t;
using u16  = uint16_t;
using u32  = uint32_t;
using u64  = uint64_t;
using s32  = int32_t;
using s64  = int64_t;
using u128 = unsigned __int128;

// ─── Board constants ─────────────────────────────────────────────────────────
static constexpr int TOTAL_SQUARES = 81;   // 9×9
static constexpr int MACRO_COUNT   = 9;    // 3×3 macro-boards
static constexpr int CELL_COUNT    = 9;    // cells per macro-board

// ─── Search constants ────────────────────────────────────────────────────────
static constexpr int INF_SCORE     = 1'000'000;
static constexpr int WIN_SCORE     = 900'000;   // terminal win
static constexpr int MAX_PLY       = 24;        // absolute depth cap
static constexpr int MAX_MOVES     = 81;        // theoretical max branching

// ─── Transposition Table ─────────────────────────────────────────────────────
// Each entry is 32 bytes; we pre-allocate up to 10 GB.
static constexpr u64 TT_SIZE_ENTRIES = (10ULL * 1024 * 1024 * 1024) / 32ULL;

enum TTFlag : u8 { TT_EXACT = 0, TT_LOWER = 1, TT_UPPER = 2 };

struct alignas(32) TTEntry {
    u64    key;       // Zobrist hash (full 64-bit)
    s32    score;     // stored score
    u8     depth;     // search depth
    u8     flag;      // TTFlag
    u8     best_move; // index 0‥80
    u8     _pad;
    u64    _pad2;     // pad to 32 bytes
};
static_assert(sizeof(TTEntry) == 32, "TTEntry must be 32 bytes");

// ─── Move representation ─────────────────────────────────────────────────────
// A move is simply the global bit-index (0‥80).
using Move = u8;
static constexpr Move NO_MOVE = 0xFF;

// ─── Forward declarations ─────────────────────────────────────────────────────
struct GameState;
struct SearchInfo;
void   init_zobrist();
u64    compute_hash(const GameState& gs);

#endif // UTTT_ENGINE_H
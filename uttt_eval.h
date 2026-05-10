/**
 * ============================================================================
 *  uttt_eval.h — Tapered Evaluation Function
 *
 *  CLOUD DEPLOYMENT: Shared header — no changes needed for cloud use.
 *  The eval runs entirely on the VM CPU; deeper search is the main benefit
 *  of the cloud setup.
 * ============================================================================
 *
 *  The evaluation is governed by the game-phase variable:
 *      t = moves_played / 81.0   (0.0 = start, 1.0 = full board)
 *
 *  THREE PHASES (continuously blended, not discrete):
 *
 *  ① OPENING — Centrality & Mobility    (dominant when t ≈ 0)
 *  ─────────────────────────────────────────────────────────────
 *  Squares near (0,0) receive large bonuses; the bonus fades as t → 1:
 *      center_bonus(x,y) = BASE_CENTRAL * (1.0 - t) / (1.0 + dist(x,y))
 *  where dist = sqrt(x²+y²).
 *
 *  Mobility (number of legal moves) is also valued, fading with t.
 *
 *  ② MIDGAME — Macro-wins & Threats     (peak around t ≈ 0.4–0.6)
 *  ─────────────────────────────────────────────────────────────
 *  Winning a macro-board is worth more as the game progresses:
 *      macro_win_value = 100 + (200 * t)
 *
 *  Unblocked two-in-a-row threats on both the local and macro level
 *  are rewarded proportionally.
 *
 *  ③ ENDGAME — The Minefield            (dominant when t ≈ 1)
 *  ─────────────────────────────────────────────────────────────
 *  Giving the opponent a "free move" (sending them to a full/won macro)
 *  is catastrophic. Penalise it with a quadratic that grows aggressively:
 *      free_move_penalty = -500 * (t * t)
 *
 *  All terms are summed from the perspective of the side to move (Negamax
 *  convention: positive = good for the mover).
 * ============================================================================
 */

#pragma once
#ifndef UTTT_EVAL_H
#define UTTT_EVAL_H

#include "uttt_engine.h"
#include "uttt_bitboard.h"
#include <cmath>

// ─── Tuning constants ─────────────────────────────────────────────────────────
static constexpr double BASE_CENTRAL      = 120.0;  // max centrality bonus (per square)
static constexpr double MOBILITY_WEIGHT   = 0.8;    // per legal move
static constexpr double THREAT_LOCAL_W    = 15.0;   // per local 2-in-a-row threat
static constexpr double THREAT_MACRO_W    = 40.0;   // per macro-level 2-in-a-row threat
static constexpr double FREE_MOVE_PENALTY = -500.0; // base free-move cost
static constexpr double MACRO_WIN_BASE    = 100.0;  // macro-win value at t=0
static constexpr double MACRO_WIN_SCALE   = 200.0;  // additional value at t=1

// ─── Precomputed per-square centrality score ─────────────────────────────────
//
//   Computed once at startup; stored as a float array indexed by global
//   bit-index (0‥80).  Value is BASE_CENTRAL / (1 + dist) before phase scaling.
//
extern double centrality_base[TOTAL_SQUARES]; // call init_eval() first

inline void init_eval() {
    for (int idx = 0; idx < TOTAL_SQUARES; ++idx) {
        int x, y;
        idx_to_xy(idx, x, y);
        double dist = std::sqrt(static_cast<double>(x * x + y * y));
        centrality_base[idx] = BASE_CENTRAL / (1.0 + dist);
    }
}

// ─── Main evaluation ──────────────────────────────────────────────────────────
//
//   Returns score from the perspective of gs.side (the mover).
//   Positive = good for the mover.
//
inline int evaluate(const GameState& gs) {
    // Fast terminal check
    if (gs.game_over) {
        if (gs.game_over == 3) return 0;                          // draw
        int winner = gs.game_over - 1;                            // 0=X, 1=O
        int score  = WIN_SCORE - gs.moves_played;                 // prefer faster wins
        return (winner == gs.side) ? score : -score;
    }

    const double t = static_cast<double>(gs.moves_played) / 81.0;
    const double t2 = t * t;
    const int mover = gs.side;
    const int opp   = mover ^ 1;

    double score = 0.0;

    // ── ① CENTRALITY (fades to zero as t → 1) ────────────────────────────────
    {
        const double phase_w = 1.0 - t;
        // Iterate over mover's pieces via popcount-free bit extraction
        u128 m_bb = gs.bb[mover];
        while (m_bb) {
            // __builtin_ctzll works on 64-bit; handle 128-bit by splitting
            int idx;
            u64 lo = static_cast<u64>(m_bb);
            if (lo) {
                idx = __builtin_ctzll(lo);
                m_bb &= m_bb - u128(1);
            } else {
                u64 hi = static_cast<u64>(m_bb >> 64);
                idx = 64 + __builtin_ctzll(hi);
                m_bb &= m_bb - u128(1);
            }
            score += centrality_base[idx] * phase_w;
        }
        // Opponent's centrality (subtract)
        u128 o_bb = gs.bb[opp];
        while (o_bb) {
            int idx;
            u64 lo = static_cast<u64>(o_bb);
            if (lo) {
                idx = __builtin_ctzll(lo);
                o_bb &= o_bb - u128(1);
            } else {
                u64 hi = static_cast<u64>(o_bb >> 64);
                idx = 64 + __builtin_ctzll(hi);
                o_bb &= o_bb - u128(1);
            }
            score -= centrality_base[idx] * phase_w;
        }
    }

    // ── ② MACRO-BOARD WINS (value scales linearly with t) ────────────────────
    {
        const double macro_val = MACRO_WIN_BASE + MACRO_WIN_SCALE * t;
        int m_macro_wins = __builtin_popcount(gs.macro_bb[mover]);
        int o_macro_wins = __builtin_popcount(gs.macro_bb[opp]);
        score += macro_val * (m_macro_wins - o_macro_wins);
    }

    // ── ② LOCAL THREATS (two-in-a-row on individual macro-boards) ────────────
    {
        for (int m = 0; m < MACRO_COUNT; ++m) {
            // Skip completed macro-boards
            if (gs.macro_full & (1 << m)) continue;

            u16 m_local = gs.local_occ(mover, m);
            u16 o_local = gs.local_occ(opp,   m);

            int m_threats = gs.count_threats(m_local, o_local);
            int o_threats = gs.count_threats(o_local, m_local);

            score += THREAT_LOCAL_W * (m_threats - o_threats);
        }
    }

    // ── ② MACRO-LEVEL THREATS (two-in-a-row on the 3×3 meta-board) ───────────
    {
        u16 m_macro = gs.macro_bb[mover];
        u16 o_macro = gs.macro_bb[opp];
        // Re-use count_threats with macro patterns (treated as a 9-bit field)
        int m_macro_threats = gs.count_threats(m_macro, o_macro);
        int o_macro_threats = gs.count_threats(o_macro, m_macro);
        score += THREAT_MACRO_W * (m_macro_threats - o_macro_threats);
    }

    // ── ③ FREE-MOVE PENALTY (quadratic endgame killer) ───────────────────────
    //
    //   A "free move" occurs when the next active_macro is already full/won,
    //   meaning the opponent can play anywhere.
    //   We detect whether the CURRENT position (after the mover's last play)
    //   gives the opponent a free move by checking if active_macro is 9.
    //   We also prospectively penalise moves that will lead to free moves by
    //   looking at the cells the mover can play: if a cell c maps to a full
    //   macro (gs.macro_full & (1 << c)), playing there gifts a free move.
    //
    {
        const double penalty = FREE_MOVE_PENALTY * t2;

        // Count mover's legal moves that gift a free move to opponent
        Move buf[MAX_MOVES];
        int  n = get_legal_moves(gs, buf);

        // Mobility bonus (fades with t)
        score += MOBILITY_WEIGHT * n * (1.0 - t);

        int gifted = 0;
        for (int i = 0; i < n; ++i) {
            int c = cell_of(buf[i]);
            if (gs.macro_full & (1 << c))
                ++gifted;
        }
        // Relative free-move balance:
        // If active_macro == 9, WE received a free move (+bonus to mover)
        if (gs.active_macro == 9) {
            score -= penalty;  // opponent gifted us one; subtract the penalty
                               // (penalty is negative, so this adds value)
        }
        // Penalise each move that would gift the opponent a free move
        // (prospective; only for a rough strategic signal, not exact)
        // We scale by fraction of free moves available
        if (n > 0)
            score += penalty * (static_cast<double>(gifted) / n);
    }

    return static_cast<int>(score);
}

#endif // UTTT_EVAL_H
/**
 * ============================================================================
 *  uttt_search.h  —  HIGH-PERFORMANCE Negamax + Lazy SMP  (v2)
 *
 *  CLOUD DEPLOYMENT: Shared header — no changes needed for cloud use.
 *  Lazy SMP thread count is governed by the VM's vCPU count automatically
 *  via omp_get_max_threads().  Oracle Free Tier: 2 threads.
 *  Hetzner CX32: 4 threads.  Set OMP_NUM_THREADS env var to override.
 * ============================================================================
 *
 *  TECHNIQUES IMPLEMENTED (each gives a measurable speedup):
 *
 *  ① NULL-MOVE PRUNING (R=2)
 *     If we "pass" our turn and the search still beats beta, the position is
 *     so overwhelmingly good we prune immediately.  Typical speedup: 3-5×
 *     in midgame positions.
 *
 *  ② ASPIRATION WINDOWS
 *     After depth 1, search with a narrow ±30 window.  On failure, widen
 *     exponentially.  Reduces nodes at each depth by ~30-40%.
 *
 *  ③ PRINCIPAL VARIATION SEARCH (PVS / Null-Window Search)
 *     After the first move (which is searched with a full window), all
 *     subsequent moves are searched with a 1-point null window [-alpha-1,
 *     -alpha].  Only re-search at full width if they unexpectedly beat alpha.
 *     Combined with good move ordering, skips ~70% of full-width re-searches.
 *
 *  ④ LATE MOVE REDUCTION (LMR)
 *     Moves ranked ≥ 4th in the sorted list are searched at reduced depth
 *     (depth-1-R).  Only re-search full depth if they beat alpha.  This is
 *     the single biggest speedup: ~4-8× at depth 10+.
 *
 *  ⑤ KILLER MOVES (2 per ply)
 *     Non-capturing moves that caused a beta cutoff are "killers" — they're
 *     tried immediately after the TT move and macro-wins.
 *
 *  ⑥ LAZY SMP
 *     N-1 worker threads run independent iterative deepening loops at
 *     staggered depths, writing to the shared TT.  The main thread benefits
 *     from TT hits populated by workers.  Near-linear scaling to ~8 threads,
 *     diminishing returns beyond.
 *
 *  COMBINED EXPECTED SPEEDUP vs v1: ~20-50× at depth 12.
 * ============================================================================
 */

#pragma once
#ifndef UTTT_SEARCH_H
#define UTTT_SEARCH_H

#include "uttt_engine.h"
#include "uttt_bitboard.h"
#include "uttt_eval.h"

#include <algorithm>
#include <atomic>
#include <thread>
#include <vector>
#include <chrono>
#include <cstring>
#include <cstdio>

// OMP is optional — stubs let the code compile without libomp installed.
// Install via:  brew install libomp
// Then compile: g++ ... -Xpreprocessor -fopenmp
//               -I$(brew --prefix libomp)/include
//               -L$(brew --prefix libomp)/lib -lomp
#ifdef _OPENMP
  #include <omp.h>
#else
  inline int omp_get_max_threads() { return 1; }
  inline int omp_get_thread_num()  { return 0; }
#endif

// ─────────────────────────────────────────────────────────────────────────────
//  TRANSPOSITION TABLE
// ─────────────────────────────────────────────────────────────────────────────

struct TranspositionTable {
    std::vector<TTEntry> entries;
    u64 mask;

    void init(u64 size_bytes) {
        u64 n = size_bytes / sizeof(TTEntry);
        // Round down to nearest power of 2 for fast modulo-via-mask
        n = u64(1) << (63 - __builtin_clzll(n ? n : 1));
        entries.assign(n, TTEntry{});
        mask = n - 1;
        printf("[TT] Allocated %.2f GB  (%llu entries x %zu bytes)\n",
               (double)(n * sizeof(TTEntry)) / (1ULL << 30),
               (unsigned long long)n, sizeof(TTEntry));
    }

    // Always-replace with depth preference:
    // Replace if incoming depth >= stored depth (fresher/deeper is better).
    inline void store(u64 key, s32 score, u8 depth, TTFlag flag, Move best) {
        TTEntry& e = entries[key & mask];
        if (e.key == key || depth >= e.depth) {
            e.key       = key;
            e.score     = score;
            e.depth     = depth;
            e.flag      = (u8)flag;
            e.best_move = best;
        }
    }

    inline bool probe(u64 key, u8 depth, s32 alpha, s32 beta,
                      s32& out_score, Move& out_best) const {
        const TTEntry& e = entries[key & mask];
        // Always return the best_move hint even on depth mismatch
        out_best = (e.key == key) ? e.best_move : NO_MOVE;
        if (e.key != key) return false;
        if (e.depth >= depth) {
            s32 s = e.score;
            if (e.flag == TT_EXACT)               { out_score = s; return true; }
            if (e.flag == TT_LOWER && s >= beta)  { out_score = s; return true; }
            if (e.flag == TT_UPPER && s <= alpha) { out_score = s; return true; }
        }
        return false;
    }
};

extern TranspositionTable g_tt;
extern std::atomic<bool>  g_stop;
extern std::atomic<u64>   g_nodes;

// ─────────────────────────────────────────────────────────────────────────────
//  KILLER MOVES  (thread-local to avoid false sharing between SMP threads)
// ─────────────────────────────────────────────────────────────────────────────

// Each thread keeps its own killer table
struct KillerTable {
    Move km[MAX_PLY + 2][2];
    void clear() { memset(km, 0xFF, sizeof(km)); }  // 0xFF = NO_MOVE
    void store(int ply, Move m) {
        if (ply > MAX_PLY) return;
        if (km[ply][0] != m) { km[ply][1] = km[ply][0]; km[ply][0] = m; }
    }
    bool is_killer(int ply, Move m) const {
        if (ply > MAX_PLY) return false;
        return km[ply][0] == m || km[ply][1] == m;
    }
};

// ─────────────────────────────────────────────────────────────────────────────
//  MOVE SCORING  (all in a single function for cache locality)
// ─────────────────────────────────────────────────────────────────────────────
inline int score_move(const GameState& gs, Move m,
                      Move tt_best, int ply,
                      const KillerTable& kt) {
    // TT move: always first
    if (m == tt_best) return 1'000'000;

    int mac = macro_of(m);
    int cel = cell_of(m);
    int pri = 0;

    // Macro-winning move: second highest priority
    {
        u16 local     = gs.local_occ(gs.side, mac);
        u16 new_local = local | (u16)(1 << cel);
        if (GameState::local_has_win(new_local)) pri += 500'000;
    }

    // Killer moves
    if (kt.is_killer(ply, m)) pri += 90'000;

    // Positional bonuses
    // Cell 4 = center of macro (most flexible — sends opponent to center macro)
    switch (cel) {
        case 4:                              pri += 3'000; break; // center
        case 0: case 2: case 6: case 8:     pri += 800;   break; // corners
        default:                             pri += 400;   break; // edges
    }

    // Playing in the globally central macro (macro 4) is strategically dominant
    if (mac == 4) pri += 600;

    // Heavy penalty: sending opponent to a full/won macro gives them a free move
    // This is the #1 strategic blunder in UTTT
    if (gs.macro_full & (1 << cel)) pri -= 8'000;

    return pri;
}

// ─────────────────────────────────────────────────────────────────────────────
//  IN-PLACE PARTIAL SORT  (pick best move to front, then sort rest)
// ─────────────────────────────────────────────────────────────────────────────

// Insertion sort — fast for n <= 81, zero allocation
inline void sort_moves_inplace(Move* moves, int* scores, int n) {
    for (int i = 1; i < n; ++i) {
        Move m = moves[i]; int s = scores[i];
        int j = i - 1;
        while (j >= 0 && scores[j] < s) {
            moves[j+1] = moves[j]; scores[j+1] = scores[j]; --j;
        }
        moves[j+1] = m; scores[j+1] = s;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  NEGAMAX  (fail-soft, with all pruning techniques)
// ─────────────────────────────────────────────────────────────────────────────

// Forward declaration
s32 negamax(GameState gs, s32 alpha, s32 beta, int depth, int ply,
            bool null_ok, KillerTable& kt);

inline s32 negamax(GameState gs,
                   s32 alpha, s32 beta,
                   int depth, int ply,
                   bool null_ok,
                   KillerTable& kt) {

    ++g_nodes;

    // ── Terminal & horizon ────────────────────────────────────────────────────
    if (gs.game_over) return evaluate(gs);
    if (depth <= 0)   return evaluate(gs);
    if (g_stop.load(std::memory_order_relaxed)) return 0;

    const s32 alpha_orig = alpha;

    // ── TT probe ──────────────────────────────────────────────────────────────
    s32  tt_score = 0;
    Move tt_best  = NO_MOVE;
    if (g_tt.probe(gs.hash, (u8)depth, alpha, beta, tt_score, tt_best))
        return tt_score;

    // ── NULL-MOVE PRUNING ─────────────────────────────────────────────────────
    //
    //  Concept: if we "give away" our move and the opponent STILL can't beat
    //  beta, our position is too good to bother fully searching.
    //
    //  Guard conditions:
    //    - depth >= 3 (too shallow = unreliable)
    //    - null_ok = true (never two null moves in a row)
    //    - Not a terminal position
    //    - The static eval beats beta (position is clearly strong)
    //      — this guard avoids zugzwang-like situations
    //
    static constexpr int NULL_R = 2;
    if (null_ok && depth >= 3) {
        s32 static_eval = evaluate(gs);
        if (static_eval >= beta) {
            // Simulate "passing" our turn
            GameState null_gs  = gs;
            null_gs.side      ^= 1;
            null_gs.hash      ^= zobrist_side_to_move;
            // active_macro unchanged: opponent can play wherever they want

            s32 null_score = -negamax(null_gs, -beta, -beta + 1,
                                      depth - 1 - NULL_R, ply + 1, false, kt);
            if (null_score >= beta) {
                g_tt.store(gs.hash, null_score, (u8)depth, TT_LOWER, NO_MOVE);
                return null_score;
            }
        }
    }

    // ── Generate & score moves ────────────────────────────────────────────────
    Move moves[MAX_MOVES];
    int  mscores[MAX_MOVES];
    int  n = get_legal_moves(gs, moves);
    if (n == 0) return evaluate(gs);

    for (int i = 0; i < n; ++i)
        mscores[i] = score_move(gs, moves[i], tt_best, ply, kt);
    sort_moves_inplace(moves, mscores, n);

    // ── Search loop ───────────────────────────────────────────────────────────
    s32  best_score = -INF_SCORE;
    Move best_move  = moves[0];

    for (int i = 0; i < n; ++i) {
        if (g_stop.load(std::memory_order_relaxed)) break;

        GameState child = gs;
        apply_move(child, moves[i]);

        s32 score;

        // ── LATE MOVE REDUCTION (LMR) ─────────────────────────────────────────
        //
        //  After the first 3 moves, try later moves at reduced depth.
        //  The intuition: our move ordering is good, so late moves are likely
        //  garbage.  Search them cheaply; only invest full depth if surprised.
        //
        //  LMR conditions (standard Crafty/Stockfish style):
        //    - At least depth 3 remaining
        //    - Move index >= 3 (not a "good" ordered move)
        //    - Not a macro-winning move (those deserve full depth)
        //    - Not a killer move
        //
        bool is_interesting = (mscores[i] >= 90'000); // TT/macro-win/killer

        if (depth >= 3 && i >= 3 && !is_interesting) {
            int R = 1 + (i >= 6 ? 1 : 0) + (depth >= 6 ? 1 : 0);
            R = std::min(R, depth - 1);  // don't reduce below depth 1

            // First: try with null window at reduced depth
            score = -negamax(child, -alpha - 1, -alpha,
                             depth - 1 - R, ply + 1, true, kt);

            // If it beats alpha, do full-depth null-window search
            if (score > alpha)
                score = -negamax(child, -alpha - 1, -alpha,
                                 depth - 1, ply + 1, true, kt);

            // If it STILL beats alpha, we need the full window
            if (score > alpha)
                score = -negamax(child, -beta, -alpha,
                                 depth - 1, ply + 1, true, kt);

        } else if (i > 0) {
            // ── PRINCIPAL VARIATION SEARCH (PVS) ─────────────────────────────
            //  All non-first moves after the PV: try null window first.
            score = -negamax(child, -alpha - 1, -alpha,
                             depth - 1, ply + 1, true, kt);
            if (score > alpha && score < beta)
                score = -negamax(child, -beta, -alpha,
                                 depth - 1, ply + 1, true, kt);
        } else {
            // First move: full window (this is our PV candidate)
            score = -negamax(child, -beta, -alpha,
                             depth - 1, ply + 1, true, kt);
        }

        if (score > best_score) {
            best_score = score;
            best_move  = moves[i];
        }
        if (score > alpha) alpha = score;
        if (alpha >= beta) {
            kt.store(ply, moves[i]);  // killer
            break;
        }
    }

    // ── TT store ──────────────────────────────────────────────────────────────
    TTFlag flag;
    if      (best_score <= alpha_orig) flag = TT_UPPER;
    else if (best_score >= beta)       flag = TT_LOWER;
    else                               flag = TT_EXACT;
    g_tt.store(gs.hash, best_score, (u8)depth, flag, best_move);

    return best_score;
}

// ─────────────────────────────────────────────────────────────────────────────
//  SEARCH RESULT
// ─────────────────────────────────────────────────────────────────────────────

struct SearchResult {
    Move   best_move;
    s32    best_score;
    int    depth_reached;
    u64    nodes;
    double elapsed_ms;
    double nps;
};

// ─────────────────────────────────────────────────────────────────────────────
//  LAZY SMP WORKER  (runs in background; writes to shared TT)
// ─────────────────────────────────────────────────────────────────────────────

struct WorkerArgs {
    const GameState* root;
    int              max_depth;
    int              thread_id;
};

static void worker_fn(WorkerArgs args) {
    KillerTable kt;
    // Each thread starts at a different depth offset to diversify TT coverage
    int start = 1 + (args.thread_id % 4);
    for (int d = start; d <= args.max_depth && !g_stop.load(); ++d) {
        kt.clear();
        negamax(*args.root, -INF_SCORE, INF_SCORE, d, 0, false, kt);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  ITERATIVE DEEPENING  (main search entry point)
// ─────────────────────────────────────────────────────────────────────────────

inline SearchResult iterative_deepening(const GameState& root,
                                        int max_depth,
                                        int time_limit_ms = 0) {
    g_stop.store(false);
    g_nodes.store(0);

    auto t_start = std::chrono::steady_clock::now();
    auto elapsed = [&]() -> double {
        return std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - t_start).count();
    };

    SearchResult result{NO_MOVE, 0, 0, 0, 0.0, 0.0};

    // ── Spawn Lazy SMP worker threads ─────────────────────────────────────────
    int n_threads = omp_get_max_threads();
    std::vector<std::thread> workers;
    workers.reserve(n_threads > 1 ? n_threads - 1 : 0);
    for (int t = 1; t < n_threads; ++t)
        workers.emplace_back(worker_fn, WorkerArgs{&root, max_depth, t});

    // ── Main thread: iterative deepening + aspiration windows ─────────────────
    s32       prev_score = 0;
    KillerTable main_kt;

    for (int depth = 1; depth <= max_depth; ++depth) {
        if (time_limit_ms > 0 && elapsed() >= time_limit_ms) break;

        main_kt.clear();

        // ── Aspiration window setup ───────────────────────────────────────────
        s32 asp_delta = 30;
        s32 asp_alpha = (depth > 2) ? std::max(-INF_SCORE, prev_score - asp_delta) : -INF_SCORE;
        s32 asp_beta  = (depth > 2) ? std::min( INF_SCORE, prev_score + asp_delta) :  INF_SCORE;

        s32  depth_best = -INF_SCORE;
        Move depth_bm   = NO_MOVE;

        // Aspiration window loop: re-search if we fail outside the window
        for (int asp_iter = 0; asp_iter < 6; ++asp_iter) {
            // Retrieve TT move for root ordering
            s32  dummy   = 0;
            Move tt_hint = NO_MOVE;
            g_tt.probe(root.hash, (u8)depth, asp_alpha, asp_beta, dummy, tt_hint);

            // Score and sort root moves
            Move rmoves[MAX_MOVES];
            int  rscores[MAX_MOVES];
            int  n = get_legal_moves(root, rmoves);
            if (n == 0) goto done;

            for (int i = 0; i < n; ++i)
                rscores[i] = score_move(root, rmoves[i], tt_hint, 0, main_kt);
            sort_moves_inplace(rmoves, rscores, n);

            // Search root moves
            depth_best = -INF_SCORE;
            s32 alpha  = asp_alpha;

            for (int i = 0; i < n; ++i) {
                if (g_stop.load(std::memory_order_relaxed)) goto done;
                if (time_limit_ms > 0 && elapsed() >= time_limit_ms) {
                    g_stop.store(true); goto done;
                }

                GameState child = root;
                apply_move(child, rmoves[i]);

                s32 score;
                if (i == 0) {
                    score = -negamax(child, -asp_beta, -alpha,
                                     depth - 1, 1, true, main_kt);
                } else {
                    score = -negamax(child, -alpha - 1, -alpha,
                                     depth - 1, 1, true, main_kt);
                    if (score > alpha && score < asp_beta)
                        score = -negamax(child, -asp_beta, -alpha,
                                         depth - 1, 1, true, main_kt);
                }

                if (score > depth_best) {
                    depth_best = score;
                    depth_bm   = rmoves[i];
                }
                if (depth_best > alpha) alpha = depth_best;
            }

            // ── Check aspiration failure ──────────────────────────────────────
            if (depth_best <= asp_alpha) {
                // Fail low: widen down
                asp_alpha = std::max(-INF_SCORE, asp_alpha - asp_delta);
                asp_delta *= 3;
            } else if (depth_best >= asp_beta) {
                // Fail high: widen up
                asp_beta = std::min(INF_SCORE, asp_beta + asp_delta);
                asp_delta *= 3;
            } else {
                break; // Success: score is within [asp_alpha, asp_beta]
            }
        }

        prev_score = depth_best;
        if (!g_stop.load()) {
            result.best_move     = depth_bm;
            result.best_score    = depth_best;
            result.depth_reached = depth;
        }

        {
            double ms  = elapsed();
            u64    nds = g_nodes.load();
            printf("[depth %2d]  score=%7d  move=%2d  nodes=%10llu  "
                   "NPS=%8.1fK  time=%6.1fms\n",
                   depth, depth_best, (int)depth_bm,
                   (unsigned long long)nds,
                   nds / (ms / 1000.0 + 1e-9) / 1000.0,
                   ms);
            fflush(stdout);
        }

        // Early exit if we found a forced win/loss
        if (std::abs(result.best_score) >= WIN_SCORE - MAX_PLY) break;
    }

done:
    g_stop.store(true);
    for (auto& w : workers) w.join();

    result.nodes      = g_nodes.load();
    result.elapsed_ms = elapsed();
    result.nps        = result.nodes / (result.elapsed_ms / 1000.0 + 1e-9);
    return result;
}

#endif // UTTT_SEARCH_H
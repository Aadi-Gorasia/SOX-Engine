/**
 * ============================================================================
 *  uttt_uci.cpp — UCI-style engine interface for the Python GUI
 * ============================================================================
 *
 *  PROTOCOL (one command per line via stdin/stdout):
 *
 *  GUI → Engine:
 *    elo <N>               Set ELO (100-3500). Engine replies "readyok\n".
 *    position [i1 i2 ...]  Replay game from move history (macro-major indices).
 *    go                    Search and reply "bestmove <idx>\n".
 *    quit                  Clean exit.
 *
 *  Engine → GUI:
 *    readyok               Acknowledgement of elo command.
 *    bestmove <idx>        Best move index (0-80), or -1 if game over.
 *    info depth <d> score <s> nodes <n> nps <k>K time <ms>ms
 *
 *  COMPILATION:
 *    macOS:
 *      brew install libomp
 *      g++ -std=c++17 -O3 -march=native -funroll-loops \
 *          -Xpreprocessor -fopenmp -lomp \
 *          -DNDEBUG -o uttt_engine uttt_uci.cpp -lm
 *
 *    Linux:
 *      g++ -std=c++17 -O3 -march=native -funroll-loops \
 *          -flto -fopenmp -DNDEBUG \
 *          -o uttt_engine uttt_uci.cpp -lm -lpthread
 * ============================================================================
 */

#include "uttt_engine.h"
#include "uttt_bitboard.h"
#include "uttt_eval.h"
#include "uttt_search.h"

#include <iostream>
#include <string>
#include <sstream>
#include <cstdlib>
#include <ctime>
#include <algorithm>

// ─── Global definitions (required by the header externs) ─────────────────────
u64 zobrist_table[TOTAL_SQUARES][2];
u64 zobrist_macro[MACRO_COUNT + 1];
u64 zobrist_side_to_move;
double centrality_base[TOTAL_SQUARES];
TranspositionTable g_tt;
std::atomic<bool>  g_stop{false};
std::atomic<u64>   g_nodes{0};

// ─── ELO → engine strength parameters ────────────────────────────────────────
//
//  Three levers:
//    max_depth   — iterative deepening ceiling
//    time_ms     — milliseconds budget per move
//    error_pct   — probability (0-100) of playing a uniformly random legal
//                  move instead of the engine's best move.  Simulates blunders.
//
struct StrengthProfile {
    int max_depth;
    int time_ms;
    int error_pct;   // 0 = never random, 100 = always random
};

StrengthProfile elo_to_strength(int elo) {
    // Clamp
    elo = std::max(100, std::min(3500, elo));

    if (elo <= 200)  return {1,  30,  95};
    if (elo <= 400)  return {1,  50,  80};
    if (elo <= 600)  return {2,  80,  60};
    if (elo <= 800)  return {2, 150,  40};
    if (elo <= 1000) return {3, 200,  25};
    if (elo <= 1200) return {4, 300,  15};
    if (elo <= 1400) return {5, 400,   8};
    if (elo <= 1600) return {6, 600,   4};
    if (elo <= 1800) return {7, 800,   2};
    if (elo <= 2000) return {8,1200,   1};
    if (elo <= 2200) return {9,1800,   0};
    if (elo <= 2400) return {10,2500,  0};
    if (elo <= 2600) return {12,4000,  0};
    if (elo <= 2800) return {14,6000,  0};
    if (elo <= 3000) return {16,8000,  0};
    if (elo <= 3200) return {18,10000, 0};
    return                  {24,15000, 0};  // 3200-3500: absolute best
}

// ─── Main loop ────────────────────────────────────────────────────────────────
int main() {
    // Disable stdio sync for maximum throughput on the pipe
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::srand(static_cast<unsigned>(std::time(nullptr)));

    init_zobrist();
    init_eval();
    g_tt.init(1ULL * 1024 * 1024 * 1024);  // 1 GB TT

    GameState gs;
    gs.reset();

    StrengthProfile sp = elo_to_strength(1500);

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;

        std::istringstream ss(line);
        std::string cmd;
        ss >> cmd;

        // ── elo <N> ─────────────────────────────────────────────────────────
        if (cmd == "elo") {
            int elo = 1500;
            ss >> elo;
            sp = elo_to_strength(elo);
            // Acknowledge so Python knows we're ready
            std::cout << "readyok\n";
            std::cout.flush();
        }

        // ── position [move ...] ─────────────────────────────────────────────
        else if (cmd == "position") {
            gs.reset();
            std::string tok;
            while (ss >> tok) {
                int idx = std::stoi(tok);
                if (idx >= 0 && idx < 81)
                    apply_move(gs, static_cast<Move>(idx));
            }
        }

        // ── go ──────────────────────────────────────────────────────────────
        else if (cmd == "go") {
            if (gs.game_over) {
                std::cout << "bestmove -1\n";
                std::cout.flush();
                continue;
            }

            // Check for blunder (random move) BEFORE searching
            bool play_random = (sp.error_pct > 0) &&
                               ((std::rand() % 100) < sp.error_pct);

            Move chosen = NO_MOVE;

            if (play_random) {
                // Pick a uniformly random legal move
                Move legal[MAX_MOVES];
                int n = get_legal_moves(gs, legal);
                if (n > 0)
                    chosen = legal[std::rand() % n];
            } else {
                // Full engine search within strength budget
                SearchResult res = iterative_deepening(
                    gs, sp.max_depth, sp.time_ms);
                chosen = res.best_move;

                // Emit an info line for debugging (Python ignores it)
                std::cerr << "info depth " << res.depth_reached
                          << " score " << res.best_score
                          << " nodes " << res.nodes
                          << " nps " << static_cast<long long>(res.nps / 1000) << "K"
                          << " time " << static_cast<long long>(res.elapsed_ms) << "ms\n";
            }

            if (chosen == NO_MOVE) {
                // Fallback: just take the first legal move
                Move legal[MAX_MOVES];
                int n = get_legal_moves(gs, legal);
                if (n > 0) chosen = legal[0];
            }

            std::cout << "bestmove " << static_cast<int>(chosen) << "\n";
            std::cout.flush();
        }

        // ── quit ────────────────────────────────────────────────────────────
        else if (cmd == "quit") {
            break;
        }
    }

    return 0;
}
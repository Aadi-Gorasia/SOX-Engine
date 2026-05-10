/**
 * ============================================================================
 *  uttt_main.cpp — Entry point, display, game loop  v4
 * ============================================================================
 *
 *  NOTE: This file is the local interactive entry point (human vs engine,
 *  self-play, perft).  For the cloud deployment, the engine binary is built
 *  from uttt_uci.cpp instead and managed by uttt_server.py over TCP.
 *
 *  INDEX LAYOUT REMINDER (macro-major):
 *    global index = macro * 9 + local_cell
 *    indices 0-8 = top-left macro, 9-17 = top-center, ..., 72-80 = bottom-right
 *
 *  DISPLAY:
 *    To show index `idx` at the right screen position:
 *      display_row = (macro/3)*3 + cell/3
 *      display_col = (macro%3)*3 + cell%3
 *
 *  COMPILATION (Linux VM or local):
 *    g++ -std=c++17 -O3 -march=native -funroll-loops \
 *        -flto -fopenmp -DNDEBUG -o uttt uttt_main.cpp -lm -lpthread
 *
 *  COMPILATION (macOS):
 *    brew install libomp
 *    g++ -std=c++17 -O3 -march=native -funroll-loops \
 *        -Xpreprocessor -fopenmp -lomp \
 *        -DNDEBUG -o uttt uttt_main.cpp -lm
 * ============================================================================
 */

#include "uttt_engine.h"
#include "uttt_bitboard.h"
#include "uttt_eval.h"
#include "uttt_search.h"

#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <chrono>
#include <omp.h>

// ─── Globals ──────────────────────────────────────────────────────────────────
u64 zobrist_table[TOTAL_SQUARES][2];
u64 zobrist_macro[MACRO_COUNT + 1];
u64 zobrist_side_to_move;
double centrality_base[TOTAL_SQUARES];

TranspositionTable g_tt;
std::atomic<bool>  g_stop{false};
std::atomic<u64>   g_nodes{0};

// ─── Board display ────────────────────────────────────────────────────────────
//
//  For each display position (drow 0-8, dcol 0-8):
//    global index = display_to_idx(drow, dcol)
//    macro        = (drow/3)*3 + dcol/3
//
void print_board(const GameState& gs) {
    printf("\n");
    printf("          X=-4 -3 -2 | -1  0 +1 | +2 +3 +4\n");
    printf("          +-----------+-----------+-----------+\n");

    for (int drow = 0; drow < 9; ++drow) {
        int y = -(drow - 4);
        printf("  Y=%+3d  | ", y);

        for (int dcol = 0; dcol < 9; ++dcol) {
            int idx  = display_to_idx(drow, dcol);
            int mac  = macro_of(idx);

            bool macro_won_x = (gs.macro_bb[0] >> mac) & 1;
            bool macro_won_o = (gs.macro_bb[1] >> mac) & 1;

            char ch;
            if (macro_won_x) {
                ch = 'X';
            } else if (macro_won_o) {
                ch = 'O';
            } else if ((gs.bb[0] >> idx) & 1) {
                ch = 'X';
            } else if ((gs.bb[1] >> idx) & 1) {
                ch = 'O';
            } else {
                bool active = (gs.active_macro == 9) ||
                              (gs.active_macro == (u8)mac);
                ch = active ? '*' : '.';
            }

            printf("%c ", ch);
            if (dcol == 2 || dcol == 5) printf("| ");
        }
        printf("\n");
        if (drow == 2 || drow == 5)
            printf("          +-----------+-----------+-----------+\n");
    }
    printf("          +-----------+-----------+-----------+\n\n");

    if (gs.active_macro == 9) {
        printf("  Active macro : ANY\n");
    } else {
        int cx, cy;
        macro_center_xy(gs.active_macro, cx, cy);
        printf("  Active macro : %d  (centre %+d,%+d)\n", gs.active_macro, cx, cy);
    }
    printf("  Side to move : %s\n",      gs.side == 0 ? "X" : "O");
    printf("  Moves played : %d\n",      gs.moves_played);
    printf("  Hash         : %016llx\n", (unsigned long long)gs.hash);
    printf("  Macro-wins   : X=%03x  O=%03x  Full=%03x\n\n",
           gs.macro_bb[0], gs.macro_bb[1], gs.macro_full);
}

// ─── Perft ────────────────────────────────────────────────────────────────────
u64 perft(GameState gs, int depth) {
    if (gs.game_over) return 0;
    if (depth == 0)   return 1;
    Move moves[MAX_MOVES];
    int n = get_legal_moves(gs, moves);
    u64 total = 0;
    for (int i = 0; i < n; ++i) {
        GameState child = gs;
        apply_move(child, moves[i]);
        total += perft(child, depth - 1);
    }
    return total;
}

// ─── Move parsing ─────────────────────────────────────────────────────────────
// Accepts:
//   "40"      → global index (0-80)
//   "0 0"     → x y in Cartesian [-4,+4]
int parse_move(const char* s) {
    int a, b;
    if (sscanf(s, "%d %d", &a, &b) == 2) {
        if (a >= -4 && a <= 4 && b >= -4 && b <= 4)
            return xy_to_index(a, b);
        return -1;
    }
    char* end;
    long idx = strtol(s, &end, 10);
    if (idx >= 0 && idx < 81) return (int)idx;
    return -1;
}

void print_legal_moves(const GameState& gs) {
    Move moves[MAX_MOVES];
    int n = get_legal_moves(gs, moves);
    printf("  Legal moves (%d):\n    ", n);
    for (int i = 0; i < n; ++i) {
        int x, y;
        idx_to_xy(moves[i], x, y);
        printf("  %2d(%+d,%+d)", moves[i], x, y);
        if ((i + 1) % 9 == 0) printf("\n    ");
    }
    printf("\n\n");
}

// ─── Main ─────────────────────────────────────────────────────────────────────
int main(int argc, char** argv) {
    init_zobrist();
    init_eval();

    int  max_depth     = 20;
    int  time_limit_ms = 0;
    // TT size: increase on cloud VMs with more RAM.
    // Oracle Free Tier (1 GB RAM): keep at 0 (auto → 512 MB in uttt_uci.cpp)
    // For uttt_main.cpp local use, 2 GB is a sensible local default.
    u64  tt_gb         = 2;
    bool self_play     = false;
    int  perft_depth   = 0;
    bool human_x       = true;

    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i],"--depth")    && i+1<argc) max_depth     = atoi(argv[++i]);
        if (!strcmp(argv[i],"--time")     && i+1<argc) time_limit_ms = atoi(argv[++i]);
        if (!strcmp(argv[i],"--tt-gb")    && i+1<argc) tt_gb         = atoll(argv[++i]);
        if (!strcmp(argv[i],"--self-play"))             self_play     = true;
        if (!strcmp(argv[i],"--perft")    && i+1<argc) perft_depth   = atoi(argv[++i]);
        if (!strcmp(argv[i],"--play-o"))                human_x       = false;
    }

    printf("╔══════════════════════════════════════════════════════╗\n");
    printf("║   ULTIMATE TIC-TAC-TOE — GRANDMASTER ENGINE v4      ║\n");
    printf("╚══════════════════════════════════════════════════════╝\n");
    printf("  OMP threads : %d\n",   omp_get_max_threads());
    printf("  Max depth   : %d\n",   max_depth);
    if (time_limit_ms) printf("  Time limit  : %d ms\n", time_limit_ms);
    printf("\n");

    g_tt.init(tt_gb * 1024ULL * 1024ULL * 1024ULL);

    if (perft_depth > 0) {
        GameState gs; gs.reset();
        // Known correct values: perft(1)=81, perft(2)=720, perft(3)=6336
        for (int d = 1; d <= perft_depth; ++d) {
            auto t0 = std::chrono::steady_clock::now();
            u64 n = perft(gs, d);
            double ms = std::chrono::duration<double,std::milli>(
                std::chrono::steady_clock::now()-t0).count();
            printf("  perft(%d) = %12llu   %.3fs   NPS=%.0fM\n",
                   d,(unsigned long long)n, ms/1000.0, n/ms/1000.0);
        }
        return 0;
    }

    GameState gs; gs.reset();
    printf("Move format:  index(0-80)  or  'x y'  (Cartesian)\n");
    printf("  Index layout: 0-8=top-left macro, 40=centre(0,0)\n\n");

    while (!gs.game_over) {
        print_board(gs);

        bool engine_turn = self_play ||
                           ( human_x && gs.side == 1) ||
                           (!human_x && gs.side == 0);
        Move chosen = NO_MOVE;

        if (engine_turn) {
            printf("  [ENGINE thinking...]\n");
            SearchResult res = iterative_deepening(gs, max_depth, time_limit_ms);
            chosen = res.best_move;
            int x, y;
            idx_to_xy(chosen, x, y);
            printf("\n  Engine plays: idx=%d  (%+d,%+d)  macro=%d  cell=%d\n",
                   chosen, x, y, macro_of(chosen), cell_of(chosen));
            printf("  score=%d  depth=%d  nodes=%llu  NPS=%.0fK\n\n",
                   res.best_score, res.depth_reached,
                   (unsigned long long)res.nodes, res.nps/1000.0);
        } else {
            print_legal_moves(gs);
            printf("  Your move (%s): ", gs.side == 0 ? "X" : "O");
            fflush(stdout);

            char buf[64];
            if (!fgets(buf, sizeof(buf), stdin)) break;

            int idx = parse_move(buf);
            if (idx < 0) { printf("  Invalid input.\n\n"); continue; }

            Move legal[MAX_MOVES];
            int  n = get_legal_moves(gs, legal);
            bool ok = false;
            for (int i = 0; i < n; ++i) if (legal[i] == idx) { ok=true; break; }
            if (!ok) {
                printf("  Illegal! Must play in macro %d. Try again.\n\n",
                       gs.active_macro);
                continue;
            }
            chosen = (Move)idx;
        }
        apply_move(gs, chosen);
    }

    print_board(gs);
    switch (gs.game_over) {
        case 1: printf("  ★  X WINS!  ★\n\n"); break;
        case 2: printf("  ★  O WINS!  ★\n\n"); break;
        case 3: printf("  ═══  DRAW  ═══\n\n"); break;
    }
    return 0;
}
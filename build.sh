#!/bin/bash
g++ -std=c++17 -O3 -march=native -funroll-loops \
    -flto -fopenmp -DNDEBUG \
    -o uttt_engine uttt_uci.cpp -lm -lpthread
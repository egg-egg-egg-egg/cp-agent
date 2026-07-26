// Validator template — testlib-based (registerValidation convention)
// Usage: ./validator [--testOverviewLogFileName <log>] < input_file
// 变量名参数（readInt 第三个参数）用于边界覆盖统计，必须填写。
#include "testlib.h"
#include <iostream>
using namespace std;

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    // Read and validate input from stdin
    int n = inf.readInt(1, 1000000, "n");
    inf.readEoln();

    for (int i = 0; i < n; i++) {
        inf.readInt(1, 1000000, "a[i]");
        if (i + 1 < n) inf.readSpace();
    }
    inf.readEoln();
    inf.readEof();

    return 0;
}

// Checker (special judge) template — testlib-based
// 仅多解题需要：答案不唯一 / 任意合法方案 / 浮点误差 / 无序输出
// Usage: ./checker <input> <participant_output> <jury_answer>
// Exit codes (testlib convention): 0=AC, 1=WA, 2=PE, 3=FAIL
#include "testlib.h"
#include <iostream>
using namespace std;

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    // inf = 测试输入, ouf = 被检查的输出, ans = 标程答案（仅作参照）
    //
    // 多解题的关键：必须仅凭 inf（+可选 ans 里的目标值）验证 ouf 的合法性/最优性，
    // 不能直接和 ans 逐字比较——对拍时 naive 输出只是"某一个"合法解。
    //
    // 示例：验证输出是一个 1..n 的排列且相邻元素之差不为 1
    // int n = inf.readInt();
    // vector<int> p(n);
    // for (int i = 0; i < n; i++) p[i] = ouf.readInt(1, n, "p[i]");
    // ... 验证合法性，不合法时 quitf(_wa, "explanation ...");

    quitf(_ok, "correct");
}

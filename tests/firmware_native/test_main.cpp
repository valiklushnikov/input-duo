#include "test_support.hpp"

#include <exception>
#include <iostream>

namespace duo::test {

namespace {
TestCase* test_cases = nullptr;
int failures = 0;
}

TestCase*& first_test_case() { return test_cases; }

int failure_count() { return failures; }

Registrar::Registrar(const char* name, void (*function)())
    : test_case_{name, function, first_test_case()} {
    first_test_case() = &test_case_;
}

void record_failure(const char* expression, const char* file, int line) {
    ++failures;
    std::cerr << file << ':' << line << ": check failed: " << expression << '\n';
}

}  // namespace duo::test

int main() {
    for (duo::test::TestCase* test_case = duo::test::first_test_case();
         test_case != nullptr; test_case = test_case->next) {
        try {
            test_case->function();
        } catch (const std::exception& exception) {
            duo::test::record_failure(exception.what(), __FILE__, __LINE__);
        } catch (...) {
            duo::test::record_failure("unknown exception", __FILE__, __LINE__);
        }
    }

    return duo::test::failure_count() == 0 ? 0 : 1;
}

#pragma once

namespace duo::test {

struct TestCase {
    const char* name;
    void (*function)();
    TestCase* next;
};

TestCase*& first_test_case();
int failure_count();
void record_failure(const char* expression, const char* file, int line);

class Registrar {
public:
    Registrar(const char* name, void (*function)());

private:
    TestCase test_case_;
};

}  // namespace duo::test

#define DUO_TEST_CONCATENATE_IMPL(left, right) left##right
#define DUO_TEST_CONCATENATE(left, right) DUO_TEST_CONCATENATE_IMPL(left, right)

#define TEST_CASE(name)                                                        \
    static void name();                                                        \
    static ::duo::test::Registrar DUO_TEST_CONCATENATE(duo_test_registrar_,    \
                                                         name)(#name, &name);   \
    static void name()

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                   \
            ::duo::test::record_failure(#expression, __FILE__, __LINE__);     \
        }                                                                       \
    } while (false)

#define CHECK_FALSE(expression) CHECK(!(expression))

#define CHECK_EQ(actual, expected)                                             \
    do {                                                                        \
        const auto& duo_actual = (actual);                                     \
        const auto& duo_expected = (expected);                                 \
        if (!(duo_actual == duo_expected)) {                                   \
            ::duo::test::record_failure(#actual " == " #expected, __FILE__, \
                                        __LINE__);                             \
        }                                                                       \
    } while (false)

# One date for the whole build, and it comes from the source.
#
# The Pico SDK stamps the build date into every image's binary info block, and
# by default that date is the preprocessor's ``__DATE__`` - the wall clock of
# whichever machine happened to run the compiler. Two builds of one commit then
# differ by two bytes, ``SHA256SUMS.txt`` describes one afternoon rather than
# one commit, and "reproducible from clean checkout" is not true.
#
# The field is worth keeping; reading it off the clock is not. So it is derived
# here instead:
#
#   1. ``SOURCE_DATE_EPOCH`` from the environment, if set. This is the
#      cross-ecosystem convention for exactly this problem, and honouring it
#      lets external reproducible-build tooling pin the value without knowing
#      anything about this project.
#   2. Otherwise the committer date of ``HEAD``. A commit is the thing a
#      release claims to be, so the commit's own date is the honest answer, and
#      it is available in any clean checkout without extra setup.
#
# Deriving it in CMake rather than only honouring the environment variable is
# deliberate: CMake cannot set the compiler's environment, so relying on
# ``SOURCE_DATE_EPOCH`` alone would leave a plain ``cmake --build`` from a
# fresh clone baking in the clock again. Reproducibility has to be a property
# of the build system, not of how somebody happened to invoke it.

# ``__DATE__`` spells months this way, in the C locale, always. Formatting the
# month with ``%b`` would hand the image over to the builder's locale, which is
# the class of problem this file exists to remove.
set(_DUO_MONTHS Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec)

function(duo_source_date_epoch out_var out_origin)
    if(DEFINED ENV{SOURCE_DATE_EPOCH} AND NOT "$ENV{SOURCE_DATE_EPOCH}" STREQUAL "")
        set(${out_var} "$ENV{SOURCE_DATE_EPOCH}" PARENT_SCOPE)
        set(${out_origin} "SOURCE_DATE_EPOCH" PARENT_SCOPE)
        return()
    endif()

    find_package(Git QUIET)
    if(GIT_FOUND)
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" -C "${CMAKE_SOURCE_DIR}" log -1 --format=%ct
            OUTPUT_VARIABLE _epoch
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET
            RESULT_VARIABLE _status
        )
        if(_status EQUAL 0 AND _epoch MATCHES "^[0-9]+$")
            # The date is fixed at configure time, so a commit landing after
            # that would leave a stale date in an incrementally rebuilt image.
            # Re-configure whenever HEAD moves. --git-path resolves this even
            # in a linked worktree, where .git is a file that never changes.
            execute_process(
                COMMAND "${GIT_EXECUTABLE}" -C "${CMAKE_SOURCE_DIR}" rev-parse --git-path HEAD
                OUTPUT_VARIABLE _head_file
                OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_QUIET
                RESULT_VARIABLE _head_status
            )
            # --git-path answers relative to the repository in a plain
            # checkout and absolutely in a worktree; BASE_DIR handles both.
            if(_head_status EQUAL 0)
                get_filename_component(_head_file "${_head_file}" ABSOLUTE
                    BASE_DIR "${CMAKE_SOURCE_DIR}")
                if(EXISTS "${_head_file}")
                    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_head_file}")
                endif()
            endif()

            set(${out_var} "${_epoch}" PARENT_SCOPE)
            set(${out_origin} "the HEAD commit" PARENT_SCOPE)
            return()
        endif()
    endif()

    # No environment variable and no git history - a source archive, most
    # likely. Falling back to the clock would silently restore the defect, so
    # fall back to a fixed instant and say so out loud: the images stay
    # reproducible, and the date in them is visibly not a real build date.
    message(WARNING
        "No SOURCE_DATE_EPOCH and no git history; the firmware build date will read "
        "'Jan  1 1970'. Set SOURCE_DATE_EPOCH to stamp a meaningful date.")
    set(${out_var} "0" PARENT_SCOPE)
    set(${out_origin} "a fallback constant" PARENT_SCOPE)
endfunction()

# Sets ``out_var`` to that instant spelled the way ``__DATE__`` spells it:
# ``Mmm dd yyyy``, with a space-padded day.
function(duo_source_date_string out_var out_origin)
    duo_source_date_epoch(_epoch _origin)

    # ``string(TIMESTAMP)`` reads SOURCE_DATE_EPOCH from the environment, which
    # is the only way to ask CMake to format an arbitrary instant. UTC, so that
    # the builder's time zone cannot move the date across midnight.
    set(_restore_set FALSE)
    if(DEFINED ENV{SOURCE_DATE_EPOCH})
        set(_restore "$ENV{SOURCE_DATE_EPOCH}")
        set(_restore_set TRUE)
    endif()
    set(ENV{SOURCE_DATE_EPOCH} "${_epoch}")
    string(TIMESTAMP _year "%Y" UTC)
    string(TIMESTAMP _month "%m" UTC)
    string(TIMESTAMP _day "%d" UTC)
    if(_restore_set)
        set(ENV{SOURCE_DATE_EPOCH} "${_restore}")
    else()
        unset(ENV{SOURCE_DATE_EPOCH})
    endif()

    string(REGEX REPLACE "^0" "" _month_index "${_month}")
    math(EXPR _month_index "${_month_index} - 1")
    list(GET _DUO_MONTHS ${_month_index} _month_name)

    # ``__DATE__`` pads single-digit days with a space, not a zero.
    string(REGEX REPLACE "^0" " " _day "${_day}")

    set(${out_var} "${_month_name} ${_day} ${_year}" PARENT_SCOPE)
    set(${out_origin} "${_origin}" PARENT_SCOPE)
endfunction()

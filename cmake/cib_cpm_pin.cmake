# Pre-register CIB transitive dependencies to suppress false-positive CPM
# version-mismatch warnings.
#
# CIB pins cpp-baremetal-concurrency@536bdd7 and cpp-std-extensions@c932eba.
# Its transitive dep cpp-baremetal-senders-and-receivers later requests them
# at 63230eb and 2b7917f. CPM treats git hash prefixes as integers, so it
# warns "63230 > 536" — even though 536bdd7 is the NEWER commit in git
# history. Pre-registering with VERSION 99999 ensures all subsequent CPM
# requests satisfy the "included >= requested" check silently.
#
# Must be invoked AFTER include(cmake/get_cpm.cmake) and BEFORE
# FetchContent_MakeAvailable(cib).

function(nova_pin_cib_transitive_packages)
    CPMAddPackage(
        NAME cpp-baremetal-concurrency
        GITHUB_REPOSITORY intel/cpp-baremetal-concurrency
        GIT_TAG 536bdd7
        VERSION 99999
    )
    CPMAddPackage(
        NAME cpp-std-extensions
        GITHUB_REPOSITORY intel/cpp-std-extensions
        GIT_TAG c932eba
        VERSION 99999
    )
endfunction()

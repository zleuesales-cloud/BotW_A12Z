#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

extern "C" int totk_spirv_cross_msl(const uint32_t *, size_t, uint32_t, char **, char **);
extern "C" int totk_spirv_cross_msl_abi(const uint32_t *, size_t, uint32_t, int, char **, char **);
extern "C" void totk_spirv_cross_free(char *);

int main(int argc, char **argv)
{
	if (argc != 2)
	{
		std::cerr << "usage: spirv_cross_abi_test <resource-bearing.spv>\n";
		return 2;
	}
	std::ifstream input(argv[1], std::ios::binary);
	std::vector<char> bytes((std::istreambuf_iterator<char>(input)), {});
	if (!input.is_open() || bytes.empty() || bytes.size() % sizeof(uint32_t) != 0)
		return 3;
	std::vector<uint32_t> words(bytes.size() / sizeof(uint32_t));
	std::memcpy(words.data(), bytes.data(), bytes.size());

	char *ab = nullptr, *ab_error = nullptr, *direct = nullptr, *direct_error = nullptr;
	int ab_result = totk_spirv_cross_msl(words.data(), words.size(), 20300, &ab, &ab_error);
	int direct_result = totk_spirv_cross_msl_abi(words.data(), words.size(), 20300, 0, &direct, &direct_error);
	if (ab_result != 0 || direct_result != 0)
	{
		std::cerr << (ab_error ? ab_error : "") << " " << (direct_error ? direct_error : "") << "\n";
		totk_spirv_cross_free(ab); totk_spirv_cross_free(ab_error);
		totk_spirv_cross_free(direct); totk_spirv_cross_free(direct_error);
		return 1;
	}
	std::string ab_msl(ab), direct_msl(direct);
	bool legacy_is_ab = ab_msl.find("spvDescriptorSetBuffer0") != std::string::npos;
	bool versioned_is_discrete = direct_msl.find("spvDescriptorSetBuffer0") == std::string::npos &&
	                             direct_msl.find("[[buffer(") != std::string::npos;
	totk_spirv_cross_free(ab); totk_spirv_cross_free(ab_error);
	totk_spirv_cross_free(direct); totk_spirv_cross_free(direct_error);
	if (!legacy_is_ab || !versioned_is_discrete)
		return 4;
	std::cout << "legacy five-argument ABI stayed AB; versioned ABI emitted direct slots\n";
	return 0;
}

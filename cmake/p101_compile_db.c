/*
 * Dependency-free compile-database helper used by the shared CMake graph.
 *
 * This program intentionally uses only the C/POSIX runtime.  It is compiled
 * as an internal target in each level-3 build, before the workspace libraries
 * or maintained host tools are available.
 */

#define _XOPEN_SOURCE 700
#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum json_kind { JSON_OBJECT, JSON_ARRAY, JSON_STRING, JSON_PRIMITIVE };

struct json_token {
  enum json_kind kind;
  size_t start;
  size_t end;
  size_t parent;
  size_t children;
};

struct json_document {
  char *text;
  size_t size;
  struct json_token *tokens;
  size_t count;
  size_t capacity;
};

struct string_list {
  char **items;
  size_t count;
  size_t capacity;
};

struct compile_entry {
  char *directory;
  char *file;
  char *output;
  struct string_list arguments;
};

struct compile_database {
  struct compile_entry *entries;
  size_t count;
};

struct child_result {
  char *output;
  size_t output_size;
  int status;
  bool timed_out;
};

struct flag_occurrence {
  char *token;
  char *base;
  char *value;
  char *file;
  size_t line;
  size_t order;
  bool negated;
};

struct flag_occurrence_list {
  struct flag_occurrence *items;
  size_t count;
  size_t capacity;
};

struct sha256_state {
  uint32_t words[8];
  uint64_t bit_count;
  unsigned char block[64];
  size_t block_size;
};

struct stack_artifact {
  char *path;
  size_t bytes;
  char sha256[65];
};

struct stack_artifact_list {
  struct stack_artifact *items;
  size_t count;
};

struct lesson_entries {
  struct string_list ids;
  struct string_list lesson_ids;
  struct string_list paths;
  struct string_list urls;
};

struct performance_record {
  char *id;
  char *input_identity;
  char *result;
};

struct performance_sample {
  struct performance_record *records;
  size_t record_count;
  uint64_t elapsed_ns;
};

struct performance_sample_list {
  struct performance_sample *items;
  size_t count;
  size_t capacity;
};

static const size_t JSON_NO_PARENT = SIZE_MAX;
static const char STACK_SCHEMA[] = "p101-stack-contract-v1";
static const char STACK_RECEIPT_SCHEMA[] = "p101-stack-contract-receipt-v1";
static const char STACK_DOES_NOT_PROVE[] =
    "This contract binds the declared stack policy bytes. It does not prove "
    "that the policy is complete, correct, or sufficient for an undeclared "
    "platform.";

static void usage(const char *program);
static int sanitize_main(int argc, char **argv);
static int analyze_main(int argc, char **argv);
static int ctu_main(int argc, char **argv);
static int format_workspace_main(int argc, char **argv);
static int flags_render_main(int argc, char **argv);
static int flags_lint_main(int argc, char **argv);
static int stack_contract_main(int argc, char **argv);
static int inspect_rule_catalog_main(int argc, char **argv);
static int tool_lesson_catalog_main(int argc, char **argv);
static int compare_performance_main(int argc, char **argv);

static bool read_file(const char *path, char **text, size_t *size);
static bool write_file(const char *path, const char *text, size_t size);
static bool make_directories(const char *path);
static char *duplicate_text(const char *text);
static char *duplicate_range(const char *text, size_t size);
static bool string_list_add(struct string_list *list, char *value);
static void string_list_destroy(struct string_list *list);
static bool string_list_copy_range(struct string_list *destination,
                                   const struct string_list *source,
                                   size_t start);

static void json_document_init(struct json_document *document);
static void json_document_destroy(struct json_document *document);
static bool json_document_load(const char *path,
                               struct json_document *document);
static bool json_parse(struct json_document *document);
static bool json_add_token(struct json_document *document, enum json_kind kind,
                           size_t start, size_t parent, size_t *index);
static bool json_is_space(char value);
static bool json_is_delimiter(char value);
static bool json_object_get(const struct json_document *document, size_t object,
                            const char *key, size_t *value);
static bool json_array_get(const struct json_document *document, size_t array,
                           size_t element, size_t *value);
static bool json_token_equals(const struct json_document *document,
                              size_t token, const char *value);
static char *json_token_string(const struct json_document *document,
                               size_t token);
static bool json_write_string(FILE *stream, const char *value);
static int json_hex_value(char value);
static bool json_token_is_false(const struct json_document *document,
                                size_t token);

static void compile_database_destroy(struct compile_database *database);
static bool compile_database_load(const char *path,
                                  struct compile_database *database);
static bool compile_entry_load(const struct json_document *document,
                               size_t object, struct compile_entry *entry);
static bool shell_split(const char *command, struct string_list *arguments);
static bool append_shell_character(char **word, size_t *length,
                                   size_t *capacity, char value);

static bool sanitize_arguments(const struct string_list *input,
                               struct string_list *output);
static bool sanitizer_should_drop(const char *argument);
static bool sanitizer_keep_f_option(const char *argument);
static bool write_sanitized_database(const char *path,
                                     const struct compile_database *database);

static bool source_file(const char *path);
static bool strip_output_arguments(const struct string_list *input,
                                   struct string_list *output);
static char *analysis_log_path(const char *directory, const char *source);
static bool run_child(char *const argv[], const char *directory,
                      unsigned int timeout_seconds,
                      struct child_result *result);
static void child_result_destroy(struct child_result *result);
static bool child_output_append(struct child_result *result, const char *data,
                                size_t size);
static int child_exit_status(int wait_status);
static bool arguments_to_vector(const struct string_list *arguments,
                                char ***vector);
static void vector_destroy(char **vector);

static bool path_is_within(const char *root, const char *path);
static char *absolute_path(const char *directory, const char *path);
static char *replace_separators(const char *path);
static bool ctu_extdef_arguments(const struct string_list *input,
                                 struct string_list *output);
static bool ctu_analysis_arguments(const struct string_list *input,
                                   struct string_list *output);
static bool starts_with(const char *text, const char *prefix);
static bool path_parent_directories(const char *path);
static bool regular_file(const char *path);
static bool format_source_name(const char *path);
static bool vendored_source(const char *relative);
static bool collect_repositories(const char *scripts_root,
                                 struct string_list *repositories);
static bool collect_tracked_sources(const struct string_list *repositories,
                                    struct string_list *sources,
                                    struct string_list *excluded);
static uint64_t file_fingerprint(const char *path, bool *read_ok);
static bool write_format_receipt(const char *path, const char *formatter,
                                 const char *version, bool check,
                                 const struct string_list *sources,
                                 const struct string_list *excluded,
                                 const struct string_list *changed,
                                 const char *workspace, bool passed);
static bool render_flag_selection(const char *selection_path,
                                  const char *output_directory, bool check,
                                  bool *in_sync);
static bool render_flag_file(const struct json_document *document,
                             size_t specification, char **output,
                             size_t *output_size,
                             struct string_list overrides[4],
                             size_t *entry_count);
static bool flag_entry_enabled(const struct json_document *document,
                               size_t entry);
static bool append_flag_override_tokens(struct string_list *list,
                                        const char *flags);
static char *normalize_active_flag_units(const char *text);
static char *joined_path(const char *directory, const char *name);
static bool string_list_contains(const struct string_list *list,
                                 const char *value);
static bool write_flag_overrides(const char *directory,
                                 struct string_list overrides[4]);
static bool stub_stale_flag_files(const char *directory,
                                  const struct string_list *selected);
static bool collect_active_flags(const char *directory,
                                 struct flag_occurrence_list *occurrences);
static bool flag_occurrence_add(struct flag_occurrence_list *list,
                                const char *token, const char *file,
                                size_t line);
static void flag_occurrence_list_destroy(struct flag_occurrence_list *list);
static bool split_flag_token(const char *token, char **base, char **value,
                             bool *negated);
static bool flag_base_is_additive(const char *base);
static int flag_strength(const char *base, const char *value, bool *known);
static int compare_string_pointers(const void *left, const void *right);
static void sha256_bytes(const unsigned char *data, size_t length,
                         char output[65]);
static uint32_t sha256_rotate(uint32_t value, unsigned int amount);
static void sha256_transform(struct sha256_state *state,
                             const unsigned char block[64]);
static void sha256_update(struct sha256_state *state, const unsigned char *data,
                          size_t length);
static void sha256_finish(struct sha256_state *state, unsigned char digest[32]);
static bool stack_paths_load(const char *scripts_root,
                             struct string_list *paths);
static bool stack_artifacts_collect(const char *scripts_root,
                                    const struct string_list *paths,
                                    struct stack_artifact_list *artifacts);
static void stack_artifact_list_destroy(struct stack_artifact_list *artifacts);
static bool stack_contract_refresh(const char *scripts_root,
                                   const char *contract_path);
static int stack_contract_verify(const char *scripts_root,
                                 const char *contract_path,
                                 const char *receipt_path);
static bool stack_contract_read(const char *contract_path,
                                const struct string_list *paths,
                                struct stack_artifact_list *artifacts);
static bool stack_contract_write(const char *contract_path,
                                 const struct stack_artifact_list *artifacts);
static bool stack_receipt_write(FILE *stream, bool passed,
                                const char *contract_sha256,
                                const struct stack_artifact_list *actual,
                                const struct string_list *mismatches);
static char *stack_artifact_path(const char *scripts_root,
                                 const char *relative);
static bool json_token_size_value(const struct json_document *document,
                                  size_t token, size_t *value);
static bool inspect_rule_catalog_render(const char *rule_directory,
                                        const char *output_path, bool check,
                                        bool *in_sync);
static bool tool_lesson_catalog_render(const char *catalog_path,
                                       const char *header_path,
                                       const char *source_path, bool check,
                                       bool *in_sync);
static bool performance_sample_add(struct performance_sample_list *samples,
                                   const char *path);
static void
performance_sample_list_destroy(struct performance_sample_list *samples);
static bool
performance_samples_match(const struct performance_sample_list *baseline,
                          const struct performance_sample_list *candidate);
static int compare_uint64_values(const void *left, const void *right);
static uint64_t performance_median(const uint64_t *values, size_t count);

int main(int argc, char **argv) {
  int status;

  status = 2;
  if (argc < 2) {
    usage(argv[0]);
    goto done;
  }
  if (strcmp(argv[1], "sanitize") == 0) {
    status = sanitize_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "analyze") == 0) {
    status = analyze_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "ctu") == 0) {
    status = ctu_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "format-workspace") == 0) {
    status = format_workspace_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "flags-render") == 0) {
    status = flags_render_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "flags-lint") == 0) {
    status = flags_lint_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "stack-contract") == 0) {
    status = stack_contract_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "inspect-rule-catalog") == 0) {
    status = inspect_rule_catalog_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "tool-lesson-catalog") == 0) {
    status = tool_lesson_catalog_main(argc - 1, argv + 1);
  } else if (strcmp(argv[1], "compare-performance") == 0) {
    status = compare_performance_main(argc - 1, argv + 1);
  } else {
    usage(argv[0]);
  }

done:
  return status;
}

static void usage(const char *program) {
  fprintf(stderr,
          "Usage:\n"
          "  %s sanitize INPUT.json OUTPUT.json\n"
          "  %s analyze COMPILE_COMMANDS.json OUTPUT_DIR FAIL_ON_DIAGNOSTIC\n"
          "  %s ctu CLANG EXTDEF COMPILE_COMMANDS.json WORK_DIR "
          "FAIL_ON_DIAGNOSTIC SOURCE_ROOT -- [ANALYZER_ARGS...]\n"
          "  %s format-workspace FORMATTER RECEIPT check|apply SCRIPTS_ROOT\n"
          "  %s flags-render SELECTION.json OUTPUT_DIR check|write\n"
          "  %s flags-lint FLAGS_DIR\n"
          "  %s stack-contract refresh|verify SCRIPTS_ROOT CONTRACT "
          "[RECEIPT|-]\n"
          "  %s inspect-rule-catalog RULE_DIRECTORY OUTPUT check|write\n"
          "  %s tool-lesson-catalog CATALOG HEADER SOURCE check|write\n",
          program, program, program, program, program, program, program,
          program, program);
  fprintf(stderr,
          "  %s compare-performance --baseline RECEIPT... --candidate "
          "RECEIPT... [--minimum-samples N] [--minimum-improvement F]\n",
          program);
}

static int inspect_rule_catalog_main(int argc, char **argv) {
  bool check;
  bool in_sync;
  bool rendered;
  int status;

  status = 2;
  if (argc != 4 ||
      (strcmp(argv[3], "check") != 0 && strcmp(argv[3], "write") != 0)) {
    usage(argv[0]);
    goto done;
  }
  check = strcmp(argv[3], "check") == 0;
  in_sync = false;
  rendered = inspect_rule_catalog_render(argv[1], argv[2], check, &in_sync);
  if (rendered) {
    status = in_sync ? 0 : 1;
  }

done:
  return status;
}

static int tool_lesson_catalog_main(int argc, char **argv) {
  bool check;
  bool in_sync;
  bool rendered;
  int status;

  status = 2;
  if (argc != 5 ||
      (strcmp(argv[4], "check") != 0 && strcmp(argv[4], "write") != 0)) {
    usage(argv[0]);
    goto done;
  }
  check = strcmp(argv[4], "check") == 0;
  in_sync = false;
  rendered =
      tool_lesson_catalog_render(argv[1], argv[2], argv[3], check, &in_sync);
  if (rendered) {
    status = in_sync ? 0 : 1;
  }

done:
  return status;
}

static int compare_performance_main(int argc, char **argv) {
  struct performance_sample_list baseline;
  struct performance_sample_list candidate;
  size_t minimum_samples;
  double minimum_improvement;
  char *end;
  size_t index;
  uint64_t *baseline_times;
  uint64_t *candidate_times;
  uint64_t baseline_median;
  uint64_t candidate_median;
  double improvement;
  bool success;
  int status;

  memset(&baseline, 0, sizeof(baseline));
  memset(&candidate, 0, sizeof(candidate));
  minimum_samples = 5U;
  minimum_improvement = 0.10;
  baseline_times = NULL;
  candidate_times = NULL;
  success = true;
  status = 2;
  for (index = 1U; success && index < (size_t)argc; index++) {
    if (strcmp(argv[index], "--baseline") == 0 && index + 1U < (size_t)argc) {
      index++;
      success = performance_sample_add(&baseline, argv[index]);
    } else if (strcmp(argv[index], "--candidate") == 0 &&
               index + 1U < (size_t)argc) {
      index++;
      success = performance_sample_add(&candidate, argv[index]);
    } else if (strcmp(argv[index], "--minimum-samples") == 0 &&
               index + 1U < (size_t)argc) {
      unsigned long parsed;

      index++;
      errno = 0;
      parsed = strtoul(argv[index], &end, 10);
      success = errno == 0 && *end == '\0' && parsed <= SIZE_MAX;
      if (success) {
        minimum_samples = (size_t)parsed;
      }
    } else if (strcmp(argv[index], "--minimum-improvement") == 0 &&
               index + 1U < (size_t)argc) {
      index++;
      errno = 0;
      minimum_improvement = strtod(argv[index], &end);
      success = errno == 0 && *end == '\0';
    } else {
      success = false;
    }
  }
  if (!success || minimum_samples < 3U || minimum_improvement <= 0.0 ||
      minimum_improvement >= 1.0 || baseline.count < minimum_samples ||
      candidate.count < minimum_samples) {
    printf("compare-check-performance: invalid performance acceptance policy "
           "or insufficient samples\n");
    goto done;
  }
  success = performance_samples_match(&baseline, &candidate);
  if (!success) {
    goto done;
  }
  baseline_times = malloc(baseline.count * sizeof(*baseline_times));
  candidate_times = malloc(candidate.count * sizeof(*candidate_times));
  success = baseline_times != NULL && candidate_times != NULL;
  for (index = 0U; success && index < baseline.count; index++) {
    baseline_times[index] = baseline.items[index].elapsed_ns;
  }
  for (index = 0U; success && index < candidate.count; index++) {
    candidate_times[index] = candidate.items[index].elapsed_ns;
  }
  if (!success) {
    goto done;
  }
  qsort(baseline_times, baseline.count, sizeof(*baseline_times),
        compare_uint64_values);
  qsort(candidate_times, candidate.count, sizeof(*candidate_times),
        compare_uint64_values);
  baseline_median = performance_median(baseline_times, baseline.count);
  candidate_median = performance_median(candidate_times, candidate.count);
  improvement = baseline_median > 0U
                    ? ((double)baseline_median - (double)candidate_median) /
                          (double)baseline_median
                    : 0.0;
  printf("check performance: %zu baseline, %zu candidate samples, %zu "
         "identity-matched nodes\n",
         baseline.count, candidate.count, baseline.items[0].record_count);
  printf("median: %.3fs -> %.3fs (%.1f%% faster)\n",
         (double)baseline_median / 1000000000.0,
         (double)candidate_median / 1000000000.0, improvement * 100.0);
  if (improvement < minimum_improvement) {
    printf("FAIL: median improvement is below %.1f%%\n",
           minimum_improvement * 100.0);
    status = 1;
  } else {
    status = 0;
  }

done:
  free(candidate_times);
  free(baseline_times);
  performance_sample_list_destroy(&candidate);
  performance_sample_list_destroy(&baseline);
  return status;
}

static int flags_render_main(int argc, char **argv) {
  bool check;
  bool in_sync;
  bool rendered;
  int status;

  status = 2;
  if (argc != 4 ||
      (strcmp(argv[3], "check") != 0 && strcmp(argv[3], "write") != 0)) {
    usage(argv[0]);
    goto done;
  }
  check = strcmp(argv[3], "check") == 0;
  in_sync = false;
  rendered = render_flag_selection(argv[1], argv[2], check, &in_sync);
  if (!rendered) {
    goto done;
  }
  status = in_sync ? 0 : 1;

done:
  return status;
}

static int flags_lint_main(int argc, char **argv) {
  struct flag_occurrence_list occurrences;
  size_t index;
  size_t problems;
  bool collected;
  int status;

  memset(&occurrences, 0, sizeof(occurrences));
  status = 2;
  if (argc != 2) {
    usage(argv[0]);
    goto done;
  }
  collected = collect_active_flags(argv[1], &occurrences);
  if (!collected || occurrences.count == 0U) {
    fprintf(stderr, "p101-bootstrap: no active flags found in %s\n", argv[1]);
    goto done;
  }
  problems = 0U;
  for (index = 0U; index < occurrences.count; index++) {
    struct flag_occurrence *current;
    struct flag_occurrence *final;
    size_t later;
    size_t earlier;
    bool seen_before;
    bool known;
    int strongest;
    int final_strength;

    current = &occurrences.items[index];
    if (flag_base_is_additive(current->base)) {
      continue;
    }
    seen_before = false;
    for (earlier = 0U; earlier < index; earlier++) {
      if (strcmp(occurrences.items[earlier].base, current->base) == 0) {
        seen_before = true;
        break;
      }
    }
    if (seen_before) {
      continue;
    }
    final = current;
    for (later = index + 1U; later < occurrences.count; later++) {
      if (strcmp(occurrences.items[later].base, current->base) == 0) {
        final = &occurrences.items[later];
      }
    }
    if (final == current) {
      continue;
    }
    for (later = index; later < occurrences.count; later++) {
      struct flag_occurrence *candidate;

      candidate = &occurrences.items[later];
      if (strcmp(candidate->base, current->base) == 0 && candidate != final &&
          candidate->negated != final->negated) {
        fprintf(stderr,
                "NEGATION %s:%zu '%s' conflicts with final %s:%zu '%s'\n",
                candidate->file, candidate->line, candidate->token, final->file,
                final->line, final->token);
        problems++;
        break;
      }
    }
    if (final->negated) {
      continue;
    }
    final_strength = flag_strength(final->base, final->value, &known);
    if (!known) {
      continue;
    }
    strongest = final_strength;
    for (later = index; later < occurrences.count; later++) {
      struct flag_occurrence *candidate;
      int strength;
      bool candidate_known;

      candidate = &occurrences.items[later];
      if (strcmp(candidate->base, current->base) != 0 || candidate->negated) {
        continue;
      }
      strength =
          flag_strength(candidate->base, candidate->value, &candidate_known);
      if (candidate_known && strength > strongest) {
        strongest = strength;
      }
    }
    if (final_strength < strongest) {
      fprintf(stderr,
              "DOWNGRADE %s: final '%s' at %s:%zu is weaker than an earlier "
              "setting\n",
              final->base, final->token, final->file, final->line);
      problems++;
    }
  }
  {
    static const char *const weak[] = {"-fpic", "-fpie"};
    static const char *const strong[] = {"-fPIC", "-fPIE"};
    size_t axis;

    for (axis = 0U; axis < 2U; axis++) {
      struct flag_occurrence *last_weak;
      struct flag_occurrence *last_strong;

      last_weak = NULL;
      last_strong = NULL;
      for (index = 0U; index < occurrences.count; index++) {
        if (strcmp(occurrences.items[index].token, weak[axis]) == 0) {
          last_weak = &occurrences.items[index];
        }
        if (strcmp(occurrences.items[index].token, strong[axis]) == 0) {
          last_strong = &occurrences.items[index];
        }
      }
      if (last_weak != NULL && last_strong != NULL &&
          last_weak->order > last_strong->order) {
        fprintf(stderr,
                "DOWNGRADE %s at %s:%zu comes after %s; weaker form wins\n",
                weak[axis], last_weak->file, last_weak->line, strong[axis]);
        problems++;
      }
    }
  }
  if (problems == 0U) {
    printf("flags/*.txt: no negation or downgrade conflicts among active "
           "flags.\n");
    status = 0;
  } else {
    fprintf(stderr, "%zu conflict(s) among active flags.\n", problems);
    status = 1;
  }

done:
  flag_occurrence_list_destroy(&occurrences);
  return status;
}

static int stack_contract_main(int argc, char **argv) {
  bool refreshed;
  int status;

  status = 2;
  if (argc < 4 || argc > 5) {
    usage(argv[0]);
    goto done;
  }
  if (strcmp(argv[1], "refresh") == 0 && argc == 4) {
    refreshed = stack_contract_refresh(argv[2], argv[3]);
    if (refreshed) {
      printf("wrote %s\n", argv[3]);
      status = 0;
    }
  } else if (strcmp(argv[1], "verify") == 0) {
    status = stack_contract_verify(argv[2], argv[3], argc == 5 ? argv[4] : "-");
  } else {
    usage(argv[0]);
  }

done:
  return status;
}

static int sanitize_main(int argc, char **argv) {
  struct compile_database database;
  bool loaded;
  bool written;
  int status;

  memset(&database, 0, sizeof(database));
  status = 2;
  if (argc != 3) {
    usage(argv[0]);
    goto done;
  }
  loaded = compile_database_load(argv[1], &database);
  if (!loaded) {
    goto done;
  }
  written = write_sanitized_database(argv[2], &database);
  if (written) {
    status = 0;
  }

done:
  compile_database_destroy(&database);
  return status;
}

static int analyze_main(int argc, char **argv) {
  struct compile_database database;
  struct string_list command;
  struct child_result child;
  char **vector;
  char *log_path;
  size_t index;
  size_t translation_units;
  bool loaded;
  bool stripped;
  bool added;
  bool converted;
  bool ran;
  bool written;
  bool have_diagnostics;
  bool child_failure;
  bool fail_on_diagnostics;
  int status;

  memset(&database, 0, sizeof(database));
  memset(&command, 0, sizeof(command));
  memset(&child, 0, sizeof(child));
  vector = NULL;
  log_path = NULL;
  translation_units = 0U;
  have_diagnostics = false;
  child_failure = false;
  status = 2;
  if (argc != 4) {
    usage(argv[0]);
    goto done;
  }
  fail_on_diagnostics = strcmp(argv[3], "1") == 0;
  if (!make_directories(argv[2])) {
    goto done;
  }
  loaded = compile_database_load(argv[1], &database);
  if (!loaded) {
    goto done;
  }
  for (index = 0U; index < database.count; index++) {
    if (!source_file(database.entries[index].file)) {
      continue;
    }
    translation_units++;
    stripped =
        strip_output_arguments(&database.entries[index].arguments, &command);
    if (!stripped || command.count == 0U) {
      child_failure = true;
      break;
    }
    added = string_list_add(&command, NULL);
    if (!added) {
      child_failure = true;
      break;
    }
    memmove(command.items + 2U, command.items + 1U,
            (command.count - 2U) * sizeof(*command.items));
    command.items[1] = duplicate_text("-fsyntax-only");
    if (command.items[1] == NULL) {
      child_failure = true;
      break;
    }
    converted = arguments_to_vector(&command, &vector);
    if (!converted) {
      child_failure = true;
      break;
    }
    ran = run_child(vector, database.entries[index].directory, 300U, &child);
    if (!ran || child.status != 0) {
      child_failure = true;
    }
    if (child.output_size > 0U) {
      have_diagnostics = true;
    }
    log_path = analysis_log_path(argv[2], database.entries[index].file);
    if (log_path == NULL) {
      child_failure = true;
      break;
    }
    written = write_file(log_path, child.output == NULL ? "" : child.output,
                         child.output_size);
    if (!written) {
      child_failure = true;
      break;
    }
    free(log_path);
    log_path = NULL;
    vector_destroy(vector);
    vector = NULL;
    child_result_destroy(&child);
    string_list_destroy(&command);
  }
  if (translation_units == 0U) {
    fprintf(stderr,
            "Analyzer (syntax-only) found no source translation units.\n");
  } else if (child_failure) {
    fprintf(stderr, "Analyzer (syntax-only) command failed. See: %s\n",
            argv[2]);
  } else if (have_diagnostics) {
    fprintf(stderr, "Analyzer (syntax-only) produced diagnostics. See: %s\n",
            argv[2]);
    status = fail_on_diagnostics ? 1 : 0;
  } else {
    status = 0;
  }

done:
  free(log_path);
  vector_destroy(vector);
  child_result_destroy(&child);
  string_list_destroy(&command);
  compile_database_destroy(&database);
  return status;
}

/* CTU is deliberately implemented in this bootstrap helper so the analyzer
 * path does not need Python.  It retains each translation unit's compiler,
 * working directory, SDK, target, definitions, and include search path. */
static int ctu_main(int argc, char **argv) {
  struct compile_database database;
  struct string_list command;
  struct string_list flags;
  struct string_list analyzer_arguments;
  struct string_list map_keys;
  struct child_result child;
  FILE *map_stream;
  char **vector;
  char *root;
  char *ast_directory;
  char *map_path;
  char *source;
  char *flat_source;
  char *ast_path;
  char *line;
  char *space;
  size_t separator;
  size_t index;
  size_t key_index;
  size_t source_entries;
  size_t admitted_entries;
  bool fail_on_diagnostics;
  bool loaded;
  bool in_tree;
  bool unique;
  bool prepared;
  bool added;
  bool ran;
  bool have_diagnostics;
  int status;

  memset(&database, 0, sizeof(database));
  memset(&command, 0, sizeof(command));
  memset(&flags, 0, sizeof(flags));
  memset(&analyzer_arguments, 0, sizeof(analyzer_arguments));
  memset(&map_keys, 0, sizeof(map_keys));
  memset(&child, 0, sizeof(child));
  map_stream = NULL;
  vector = NULL;
  root = NULL;
  ast_directory = NULL;
  map_path = NULL;
  source = NULL;
  flat_source = NULL;
  ast_path = NULL;
  line = NULL;
  have_diagnostics = false;
  source_entries = 0U;
  admitted_entries = 0U;
  status = 2;
  separator = 0U;
  while (separator < (size_t)argc && strcmp(argv[separator], "--") != 0) {
    separator++;
  }
  if (separator != 7U) {
    usage(argv[0]);
    goto done;
  }
  fail_on_diagnostics = strcmp(argv[5], "1") == 0;
  root = realpath(argv[6], NULL);
  if (root == NULL) {
    fprintf(stderr, "cannot resolve CTU source root %s: %s\n", argv[6],
            strerror(errno));
    goto done;
  }
  ast_directory = malloc(strlen(argv[4]) + sizeof("/ast"));
  if (ast_directory == NULL) {
    goto done;
  }
  sprintf(ast_directory, "%s/ast", argv[4]);
  if (!make_directories(ast_directory)) {
    goto done;
  }
  map_path = malloc(strlen(ast_directory) + sizeof("/externalDefMap.txt"));
  if (map_path == NULL) {
    goto done;
  }
  sprintf(map_path, "%s/externalDefMap.txt", ast_directory);
  map_stream = fopen(map_path, "w");
  if (map_stream == NULL) {
    fprintf(stderr, "cannot create %s: %s\n", map_path, strerror(errno));
    goto done;
  }
  loaded = compile_database_load(argv[3], &database);
  if (!loaded) {
    goto done;
  }
  for (index = 0U; index < database.count; index++) {
    if (!source_file(database.entries[index].file)) {
      continue;
    }
    source_entries++;
    source = absolute_path(database.entries[index].directory,
                           database.entries[index].file);
    in_tree = source != NULL && path_is_within(root, source);
    if (!in_tree) {
      free(source);
      source = NULL;
      continue;
    }
    unique = true;
    for (key_index = 0U; key_index < index; key_index++) {
      char *prior;

      prior = absolute_path(database.entries[key_index].directory,
                            database.entries[key_index].file);
      if (prior != NULL && strcmp(prior, source) == 0) {
        unique = false;
      }
      free(prior);
      if (!unique) {
        break;
      }
    }
    if (!unique) {
      free(source);
      source = NULL;
      continue;
    }
    admitted_entries++;
    prepared =
        strip_output_arguments(&database.entries[index].arguments, &command);
    flat_source = replace_separators(source);
    if (!prepared || flat_source == NULL) {
      goto done;
    }
    ast_path =
        malloc(strlen(ast_directory) + strlen(flat_source) + sizeof("/.ast"));
    if (ast_path == NULL) {
      goto done;
    }
    sprintf(ast_path, "%s/%s.ast", ast_directory, flat_source);
    added = string_list_add(&command, duplicate_text("-emit-ast"));
    added = added && string_list_add(&command, duplicate_text("-o"));
    added = added && string_list_add(&command, duplicate_text(ast_path));
    added = added && arguments_to_vector(&command, &vector);
    if (!added) {
      goto done;
    }
    ran = run_child(vector, database.entries[index].directory, 300U, &child);
    if (!ran || child.status != 0) {
      fprintf(stderr, "CTU emit-ast failed for %s (exit %d)\n", source,
              child.status);
      if (child.output != NULL) {
        fputs(child.output, stderr);
      }
      goto done;
    }
    vector_destroy(vector);
    vector = NULL;
    child_result_destroy(&child);
    string_list_destroy(&command);
    prepared = ctu_extdef_arguments(&database.entries[index].arguments, &flags);
    added = prepared && string_list_add(&command, duplicate_text(argv[2]));
    added = added && string_list_add(&command, duplicate_text(source));
    added = added && string_list_add(&command, duplicate_text("--"));
    added = added && string_list_copy_range(&command, &flags, 0U);
    added = added && arguments_to_vector(&command, &vector);
    if (!added) {
      goto done;
    }
    ran = run_child(vector, database.entries[index].directory, 300U, &child);
    if (!ran || child.status != 0) {
      fprintf(stderr, "CTU extdef mapping failed for %s (exit %d)\n", source,
              child.status);
      if (child.output != NULL) {
        fputs(child.output, stderr);
      }
      goto done;
    }
    line = child.output;
    while (line != NULL && *line != '\0') {
      char *next;
      bool seen;

      next = strchr(line, '\n');
      if (next != NULL) {
        *next = '\0';
      }
      space = strchr(line, ' ');
      if (space != NULL) {
        *space = '\0';
        seen = false;
        for (key_index = 0U; key_index < map_keys.count; key_index++) {
          if (strcmp(map_keys.items[key_index], line) == 0) {
            seen = true;
            break;
          }
        }
        if (!seen) {
          added = string_list_add(&map_keys, duplicate_text(line));
          if (!added || fprintf(map_stream, "%s %s\n", line, ast_path) < 0) {
            goto done;
          }
        }
      }
      line = next == NULL ? NULL : next + 1;
    }
    free(source);
    free(flat_source);
    free(ast_path);
    source = NULL;
    flat_source = NULL;
    ast_path = NULL;
    vector_destroy(vector);
    vector = NULL;
    child_result_destroy(&child);
    string_list_destroy(&command);
    string_list_destroy(&flags);
  }
  if (source_entries == 0U) {
    fprintf(
        stdout,
        "CTU analysis skipped: compile database has no translation units\n");
    status = 0;
    goto done;
  }
  if (admitted_entries == 0U) {
    fprintf(stderr, "CTU analysis found no in-tree translation units\n");
    status = 2;
    goto done;
  }
  if (fclose(map_stream) != 0) {
    map_stream = NULL;
    goto done;
  }
  map_stream = NULL;
  for (index = 0U; index < database.count; index++) {
    if (!source_file(database.entries[index].file)) {
      continue;
    }
    source = absolute_path(database.entries[index].directory,
                           database.entries[index].file);
    in_tree = source != NULL && path_is_within(root, source);
    if (!in_tree) {
      free(source);
      source = NULL;
      continue;
    }
    prepared = ctu_analysis_arguments(&database.entries[index].arguments,
                                      &analyzer_arguments);
    added = prepared && string_list_add(&command, duplicate_text(argv[1]));
    added = added && string_list_add(&command, duplicate_text("--analyze"));
    added = added &&
            string_list_add(
                &command, duplicate_text("-Wno-unused-command-line-argument"));
    for (key_index = separator + 1U; added && key_index < (size_t)argc;
         key_index++) {
      added = string_list_add(&command, duplicate_text(argv[key_index]));
    }
    added = added && string_list_add(&command, duplicate_text("-Xanalyzer"));
    added =
        added && string_list_add(&command, duplicate_text("-analyzer-config"));
    added = added && string_list_add(&command, duplicate_text("-Xanalyzer"));
    added = added &&
            string_list_add(
                &command,
                duplicate_text("experimental-enable-naive-ctu-analysis=true"));
    added = added && string_list_add(&command, duplicate_text("-Xanalyzer"));
    added =
        added && string_list_add(&command, duplicate_text("-analyzer-config"));
    added = added && string_list_add(&command, duplicate_text("-Xanalyzer"));
    if (added) {
      char *ctu_directory_argument;

      ctu_directory_argument =
          malloc(strlen(ast_directory) + sizeof("ctu-dir="));
      if (ctu_directory_argument == NULL) {
        added = false;
      } else {
        sprintf(ctu_directory_argument, "ctu-dir=%s", ast_directory);
        added = string_list_add(&command, ctu_directory_argument);
      }
    }
    added = added && string_list_copy_range(&command, &analyzer_arguments, 0U);
    added = added && arguments_to_vector(&command, &vector);
    if (!added) {
      goto done;
    }
    ran = run_child(vector, database.entries[index].directory, 300U, &child);
    if (!ran || child.status != 0) {
      fprintf(stderr, "CTU analyze failed for %s (exit %d)\n", source,
              child.status);
      if (child.output != NULL) {
        fputs(child.output, stderr);
      }
      goto done;
    }
    if (child.output_size > 0U) {
      have_diagnostics = true;
      fwrite(child.output, 1U, child.output_size, stdout);
    }
    free(source);
    source = NULL;
    vector_destroy(vector);
    vector = NULL;
    child_result_destroy(&child);
    string_list_destroy(&command);
    string_list_destroy(&analyzer_arguments);
  }
  status = have_diagnostics && fail_on_diagnostics ? 1 : 0;

done:
  if (map_stream != NULL) {
    fclose(map_stream);
  }
  free(root);
  free(ast_directory);
  free(map_path);
  free(source);
  free(flat_source);
  free(ast_path);
  vector_destroy(vector);
  child_result_destroy(&child);
  string_list_destroy(&command);
  string_list_destroy(&flags);
  string_list_destroy(&analyzer_arguments);
  string_list_destroy(&map_keys);
  compile_database_destroy(&database);
  return status;
}

static int format_workspace_main(int argc, char **argv) {
  struct string_list repositories;
  struct string_list sources;
  struct string_list excluded;
  struct string_list changed;
  struct string_list command;
  struct child_result child;
  uint64_t *before;
  char **vector;
  char *scripts_root;
  char *workspace;
  char *separator;
  char *version;
  size_t batch_start;
  size_t batch_end;
  size_t index;
  bool check;
  bool collected;
  bool read_ok;
  bool added;
  bool converted;
  bool ran;
  bool formatter_failed;
  bool receipt_written;
  int status;

  memset(&repositories, 0, sizeof(repositories));
  memset(&sources, 0, sizeof(sources));
  memset(&excluded, 0, sizeof(excluded));
  memset(&changed, 0, sizeof(changed));
  memset(&command, 0, sizeof(command));
  memset(&child, 0, sizeof(child));
  before = NULL;
  vector = NULL;
  scripts_root = NULL;
  workspace = NULL;
  version = NULL;
  formatter_failed = false;
  status = 2;
  if (argc != 5 ||
      (strcmp(argv[3], "check") != 0 && strcmp(argv[3], "apply") != 0)) {
    usage(argv[0]);
    goto done;
  }
  check = strcmp(argv[3], "check") == 0;
  scripts_root = realpath(argv[4], NULL);
  if (scripts_root == NULL) {
    fprintf(stderr, "cannot resolve scripts root %s: %s\n", argv[4],
            strerror(errno));
    goto done;
  }
  workspace = duplicate_text(scripts_root);
  if (workspace == NULL) {
    goto done;
  }
  separator = strrchr(workspace, '/');
  if (separator == NULL || separator == workspace) {
    fprintf(stderr, "scripts root has no workspace parent: %s\n", scripts_root);
    goto done;
  }
  *separator = '\0';
  collected = collect_repositories(scripts_root, &repositories);
  if (collected) {
    collected = collect_tracked_sources(&repositories, &sources, &excluded);
  }
  if (!collected) {
    goto done;
  }
  before = calloc(sources.count, sizeof(*before));
  if (sources.count > 0U && before == NULL) {
    goto done;
  }
  for (index = 0U; index < sources.count; index++) {
    before[index] = file_fingerprint(sources.items[index], &read_ok);
    if (!read_ok) {
      goto done;
    }
  }
  added = string_list_add(&command, duplicate_text(argv[1]));
  added = added && string_list_add(&command, duplicate_text("--version"));
  added = added && arguments_to_vector(&command, &vector);
  if (!added) {
    goto done;
  }
  ran = run_child(vector, scripts_root, 60U, &child);
  if (!ran || child.status != 0) {
    fprintf(stderr, "cannot query formatter version: %s\n", argv[1]);
    goto done;
  }
  version = duplicate_range(child.output == NULL ? "" : child.output,
                            child.output_size);
  if (version == NULL) {
    goto done;
  }
  separator = strpbrk(version, "\r\n");
  if (separator != NULL) {
    *separator = '\0';
  }
  vector_destroy(vector);
  vector = NULL;
  child_result_destroy(&child);
  string_list_destroy(&command);

  for (batch_start = 0U; batch_start < sources.count; batch_start = batch_end) {
    batch_end = batch_start + 128U;
    if (batch_end > sources.count) {
      batch_end = sources.count;
    }
    added = string_list_add(&command, duplicate_text(argv[1]));
    if (check) {
      added = added && string_list_add(&command, duplicate_text("--dry-run"));
      added = added && string_list_add(&command, duplicate_text("--Werror"));
    } else {
      added = added && string_list_add(&command, duplicate_text("-i"));
    }
    added = added && string_list_add(&command, duplicate_text("-style=file"));
    for (index = batch_start; added && index < batch_end; index++) {
      added = string_list_add(&command, duplicate_text(sources.items[index]));
    }
    converted = added && arguments_to_vector(&command, &vector);
    if (!converted) {
      goto done;
    }
    ran = run_child(vector, scripts_root, 300U, &child);
    if (!ran || child.status != 0) {
      formatter_failed = true;
      if (child.output != NULL) {
        fwrite(child.output, 1U, child.output_size, stdout);
      }
      if (check) {
        for (index = batch_start; index < batch_end; index++) {
          if (child.output != NULL &&
              strstr(child.output, sources.items[index]) != NULL) {
            added =
                string_list_add(&changed, duplicate_text(sources.items[index]));
            if (!added) {
              goto done;
            }
          }
        }
      } else {
        vector_destroy(vector);
        vector = NULL;
        child_result_destroy(&child);
        string_list_destroy(&command);
        break;
      }
    }
    vector_destroy(vector);
    vector = NULL;
    child_result_destroy(&child);
    string_list_destroy(&command);
  }
  for (index = 0U; index < sources.count; index++) {
    uint64_t after;
    bool seen;
    size_t changed_index;

    after = file_fingerprint(sources.items[index], &read_ok);
    if (!read_ok) {
      goto done;
    }
    if (after == before[index]) {
      continue;
    }
    seen = false;
    for (changed_index = 0U; changed_index < changed.count; changed_index++) {
      if (strcmp(changed.items[changed_index], sources.items[index]) == 0) {
        seen = true;
        break;
      }
    }
    if (!seen) {
      added = string_list_add(&changed, duplicate_text(sources.items[index]));
      if (!added) {
        goto done;
      }
    }
  }
  receipt_written = write_format_receipt(
      argv[2], argv[1], version, check, &sources, &excluded, &changed,
      workspace, !formatter_failed && (!check || changed.count == 0U));
  if (!receipt_written) {
    goto done;
  }
  if (formatter_failed) {
    fprintf(stdout, "clang-format %s in tracked source files:\n",
            check ? "found formatting drift" : "failed while formatting");
    for (index = 0U; index < changed.count; index++) {
      const char *relative;

      relative = starts_with(changed.items[index], workspace) &&
                         changed.items[index][strlen(workspace)] == '/'
                     ? changed.items[index] + strlen(workspace) + 1U
                     : changed.items[index];
      fprintf(stdout, "  %s\n", relative);
    }
    if (check) {
      fputs("Run update-all.sh locally to apply the required formatting.\n",
            stdout);
    }
    status = 1;
  } else {
    if (changed.count > 0U) {
      fputs("clang-format updated tracked source files:\n", stdout);
      for (index = 0U; index < changed.count; index++) {
        const char *relative;

        relative = starts_with(changed.items[index], workspace) &&
                           changed.items[index][strlen(workspace)] == '/'
                       ? changed.items[index] + strlen(workspace) + 1U
                       : changed.items[index];
        fprintf(stdout, "  %s\n", relative);
      }
    }
    fprintf(stdout,
            "workspace formatting (%s): %zu first-party tracked C/C++ "
            "files; %zu changed; %zu vendored files excluded\n",
            check ? "check" : "apply", sources.count, changed.count,
            excluded.count);
    status = 0;
  }

done:
  free(before);
  free(scripts_root);
  free(workspace);
  free(version);
  vector_destroy(vector);
  child_result_destroy(&child);
  string_list_destroy(&command);
  string_list_destroy(&repositories);
  string_list_destroy(&sources);
  string_list_destroy(&excluded);
  string_list_destroy(&changed);
  return status;
}

static bool read_file(const char *path, char **text, size_t *size) {
  FILE *stream;
  long length;
  size_t read_size;
  bool success;

  *text = NULL;
  *size = 0U;
  success = false;
  stream = fopen(path, "rb");
  if (stream == NULL) {
    fprintf(stderr, "cannot open %s: %s\n", path, strerror(errno));
    goto done;
  }
  if (fseek(stream, 0L, SEEK_END) != 0) {
    goto close;
  }
  length = ftell(stream);
  if (length < 0 || fseek(stream, 0L, SEEK_SET) != 0) {
    goto close;
  }
  *text = malloc((size_t)length + 1U);
  if (*text == NULL) {
    goto close;
  }
  read_size = fread(*text, 1U, (size_t)length, stream);
  if (read_size != (size_t)length) {
    free(*text);
    *text = NULL;
    goto close;
  }
  (*text)[read_size] = '\0';
  *size = read_size;
  success = true;

close:
  if (fclose(stream) != 0) {
    success = false;
  }
done:
  return success;
}

static bool stack_refusal_write(FILE *stream, const char *diagnostic,
                                const char *reason) {
  FILE *canonical_stream;
  char *canonical;
  size_t canonical_size;
  char digest[65];
  bool success;

  canonical = NULL;
  canonical_size = 0U;
  canonical_stream = open_memstream(&canonical, &canonical_size);
  success = canonical_stream != NULL;
  if (success) {
    success =
        fputs("{\"diagnostic\":", canonical_stream) >= 0 &&
        json_write_string(canonical_stream, diagnostic) &&
        fputs(",\"does_not_prove\":", canonical_stream) >= 0 &&
        json_write_string(canonical_stream, STACK_DOES_NOT_PROVE) &&
        fputs(",\"outcome\":\"refused\",\"reason\":", canonical_stream) >= 0 &&
        json_write_string(canonical_stream, reason) &&
        fputs(",\"schema\":\"p101-stack-contract-refusal-v1\"}",
              canonical_stream) >= 0;
  }
  if (canonical_stream != NULL && fclose(canonical_stream) != 0) {
    success = false;
  }
  if (!success) {
    free(canonical);
    goto done;
  }
  sha256_bytes((const unsigned char *)canonical, canonical_size, digest);
  success = fputs("{\"diagnostic\":", stream) >= 0 &&
            json_write_string(stream, diagnostic) &&
            fputs(",\"does_not_prove\":", stream) >= 0 &&
            json_write_string(stream, STACK_DOES_NOT_PROVE) &&
            fputs(",\"outcome\":\"refused\",\"reason\":", stream) >= 0 &&
            json_write_string(stream, reason) &&
            fprintf(stream,
                    ",\"receipt_digest\":\"sha256:%s\",\"schema\":"
                    "\"p101-stack-contract-refusal-v1\"}\n",
                    digest) >= 0;
  free(canonical);

done:
  return success;
}

static bool write_file(const char *path, const char *text, size_t size) {
  FILE *stream;
  size_t written;
  bool success;

  success = false;
  stream = fopen(path, "wb");
  if (stream == NULL) {
    fprintf(stderr, "cannot create %s: %s\n", path, strerror(errno));
    goto done;
  }
  written = fwrite(text, 1U, size, stream);
  success = written == size && fclose(stream) == 0;
  stream = NULL;

done:
  if (stream != NULL) {
    fclose(stream);
  }
  return success;
}

static bool make_directories(const char *path) {
  char *copy;
  char *cursor;
  bool success;

  copy = duplicate_text(path);
  success = copy != NULL;
  if (!success) {
    goto done;
  }
  for (cursor = copy + 1; *cursor != '\0'; cursor++) {
    if (*cursor == '/') {
      *cursor = '\0';
      if (mkdir(copy, 0777) != 0 && errno != EEXIST) {
        success = false;
        break;
      }
      *cursor = '/';
    }
  }
  if (success && mkdir(copy, 0777) != 0 && errno != EEXIST) {
    success = false;
  }

done:
  free(copy);
  return success;
}

static char *duplicate_text(const char *text) {
  char *copy;

  copy = NULL;
  if (text != NULL) {
    copy = duplicate_range(text, strlen(text));
  }
  return copy;
}

static char *duplicate_range(const char *text, size_t size) {
  char *copy;

  copy = malloc(size + 1U);
  if (copy != NULL) {
    memcpy(copy, text, size);
    copy[size] = '\0';
  }
  return copy;
}

static bool string_list_add(struct string_list *list, char *value) {
  char **items;
  size_t capacity;
  bool added;

  added = false;
  if (list->count == list->capacity) {
    capacity = list->capacity == 0U ? 16U : list->capacity * 2U;
    if (capacity < list->capacity ||
        capacity > SIZE_MAX / sizeof(*list->items)) {
      goto done;
    }
    items = realloc(list->items, capacity * sizeof(*list->items));
    if (items == NULL) {
      goto done;
    }
    list->items = items;
    list->capacity = capacity;
  }
  list->items[list->count] = value;
  list->count++;
  added = true;

done:
  if (!added) {
    free(value);
  }
  return added;
}

static void string_list_destroy(struct string_list *list) {
  size_t index;

  for (index = 0U; index < list->count; index++) {
    free(list->items[index]);
  }
  free(list->items);
  memset(list, 0, sizeof(*list));
}

static bool string_list_copy_range(struct string_list *destination,
                                   const struct string_list *source,
                                   size_t start) {
  size_t index;
  char *copy;
  bool success;

  success = true;
  for (index = start; index < source->count && success; index++) {
    copy = duplicate_text(source->items[index]);
    success = copy != NULL && string_list_add(destination, copy);
  }
  return success;
}

static void json_document_init(struct json_document *document) {
  memset(document, 0, sizeof(*document));
}

static void json_document_destroy(struct json_document *document) {
  free(document->tokens);
  free(document->text);
  json_document_init(document);
}

static bool json_document_load(const char *path,
                               struct json_document *document) {
  bool loaded;
  bool parsed;

  json_document_destroy(document);
  loaded = read_file(path, &document->text, &document->size);
  parsed = loaded && json_parse(document);
  if (!parsed) {
    fprintf(stderr, "invalid JSON document: %s\n", path);
    json_document_destroy(document);
  }
  return parsed;
}

static bool json_parse(struct json_document *document) {
  size_t cursor;
  size_t parent;
  size_t token;
  char value;
  bool escaped;
  bool added;
  bool parsed;

  cursor = 0U;
  parent = JSON_NO_PARENT;
  parsed = true;
  while (cursor < document->size && parsed) {
    value = document->text[cursor];
    if (json_is_space(value)) {
      cursor++;
    } else if (value == '{' || value == '[') {
      added = json_add_token(document, value == '{' ? JSON_OBJECT : JSON_ARRAY,
                             cursor, parent, &token);
      if (!added) {
        parsed = false;
      } else {
        parent = token;
        cursor++;
      }
    } else if (value == '}' || value == ']') {
      if (parent == JSON_NO_PARENT ||
          (value == '}' && document->tokens[parent].kind != JSON_OBJECT) ||
          (value == ']' && document->tokens[parent].kind != JSON_ARRAY)) {
        parsed = false;
      } else {
        document->tokens[parent].end = cursor + 1U;
        parent = document->tokens[parent].parent;
        cursor++;
      }
    } else if (value == '"') {
      cursor++;
      added = json_add_token(document, JSON_STRING, cursor, parent, &token);
      if (!added) {
        parsed = false;
        continue;
      }
      escaped = false;
      while (cursor < document->size) {
        value = document->text[cursor];
        if (!escaped && value == '"') {
          break;
        }
        if (!escaped && value == '\\') {
          escaped = true;
        } else {
          escaped = false;
        }
        cursor++;
      }
      if (cursor >= document->size) {
        parsed = false;
      } else {
        document->tokens[token].end = cursor;
        cursor++;
      }
    } else if (value == ':' || value == ',') {
      cursor++;
    } else {
      added = json_add_token(document, JSON_PRIMITIVE, cursor, parent, &token);
      if (!added) {
        parsed = false;
        continue;
      }
      while (cursor < document->size &&
             !json_is_delimiter(document->text[cursor])) {
        cursor++;
      }
      document->tokens[token].end = cursor;
    }
  }
  if (parent != JSON_NO_PARENT || document->count == 0U) {
    parsed = false;
  }
  return parsed;
}

static bool json_add_token(struct json_document *document, enum json_kind kind,
                           size_t start, size_t parent, size_t *index) {
  struct json_token *tokens;
  size_t capacity;
  bool added;

  added = false;
  if (document->count == document->capacity) {
    capacity = document->capacity == 0U ? 256U : document->capacity * 2U;
    if (capacity < document->capacity ||
        capacity > SIZE_MAX / sizeof(*document->tokens)) {
      goto done;
    }
    tokens = realloc(document->tokens, capacity * sizeof(*document->tokens));
    if (tokens == NULL) {
      goto done;
    }
    document->tokens = tokens;
    document->capacity = capacity;
  }
  *index = document->count;
  document->tokens[*index].kind = kind;
  document->tokens[*index].start = start;
  document->tokens[*index].end = start;
  document->tokens[*index].parent = parent;
  document->tokens[*index].children = 0U;
  document->count++;
  if (parent != JSON_NO_PARENT) {
    document->tokens[parent].children++;
  }
  added = true;

done:
  return added;
}

static bool json_is_space(char value) {
  bool result;

  result = value == ' ' || value == '\t' || value == '\n' || value == '\r';
  return result;
}

static bool json_is_delimiter(char value) {
  bool result;

  result = json_is_space(value) || value == ',' || value == ']' || value == '}';
  return result;
}

static bool json_object_get(const struct json_document *document, size_t object,
                            const char *key, size_t *value) {
  size_t index;
  size_t child;
  bool found;

  found = false;
  child = 0U;
  if (object >= document->count ||
      document->tokens[object].kind != JSON_OBJECT) {
    goto done;
  }
  for (index = object + 1U;
       index < document->count &&
       document->tokens[index].start < document->tokens[object].end;
       index++) {
    if (document->tokens[index].parent != object) {
      continue;
    }
    if ((child % 2U) == 0U && json_token_equals(document, index, key) &&
        index + 1U < document->count &&
        document->tokens[index + 1U].parent == object) {
      *value = index + 1U;
      found = true;
      break;
    }
    child++;
  }

done:
  return found;
}

static bool json_array_get(const struct json_document *document, size_t array,
                           size_t element, size_t *value) {
  size_t index;
  size_t child;
  bool found;

  found = false;
  child = 0U;
  if (array >= document->count || document->tokens[array].kind != JSON_ARRAY) {
    goto done;
  }
  for (index = array + 1U;
       index < document->count &&
       document->tokens[index].start < document->tokens[array].end;
       index++) {
    if (document->tokens[index].parent == array) {
      if (child == element) {
        *value = index;
        found = true;
        break;
      }
      child++;
    }
  }

done:
  return found;
}

static bool json_token_equals(const struct json_document *document,
                              size_t token, const char *value) {
  size_t token_size;
  size_t value_size;
  bool equal;

  equal = false;
  if (token < document->count) {
    token_size = document->tokens[token].end - document->tokens[token].start;
    value_size = strlen(value);
    equal = token_size == value_size &&
            strncmp(document->text + document->tokens[token].start, value,
                    token_size) == 0;
  }
  return equal;
}

static bool json_token_is_false(const struct json_document *document,
                                size_t token) {
  bool result;

  result = json_token_equals(document, token, "false");
  return result;
}

static bool json_token_size_value(const struct json_document *document,
                                  size_t token, size_t *value) {
  char *text;
  char *end;
  unsigned long long parsed;
  bool success;

  text = NULL;
  success =
      token < document->count && document->tokens[token].kind == JSON_PRIMITIVE;
  if (success) {
    text = duplicate_range(document->text + document->tokens[token].start,
                           document->tokens[token].end -
                               document->tokens[token].start);
    success = text != NULL;
  }
  if (success) {
    errno = 0;
    parsed = strtoull(text, &end, 10);
    success = errno == 0 && *end == '\0' && parsed <= SIZE_MAX;
  }
  if (success) {
    *value = (size_t)parsed;
  }
  free(text);
  return success;
}

static char *json_token_string(const struct json_document *document,
                               size_t token) {
  const char *source;
  size_t size;
  size_t read_index;
  size_t write_index;
  char *result;

  result = NULL;
  if (token >= document->count || document->tokens[token].kind != JSON_STRING) {
    goto done;
  }
  source = document->text + document->tokens[token].start;
  size = document->tokens[token].end - document->tokens[token].start;
  result = malloc(size + 1U);
  if (result == NULL) {
    goto done;
  }
  write_index = 0U;
  for (read_index = 0U; read_index < size; read_index++) {
    if (source[read_index] == '\\' && read_index + 1U < size) {
      read_index++;
      switch (source[read_index]) {
      case '"':
        result[write_index++] = '"';
        break;
      case '\\':
        result[write_index++] = '\\';
        break;
      case '/':
        result[write_index++] = '/';
        break;
      case 'b':
        result[write_index++] = '\b';
        break;
      case 'f':
        result[write_index++] = '\f';
        break;
      case 'n':
        result[write_index++] = '\n';
        break;
      case 'r':
        result[write_index++] = '\r';
        break;
      case 't':
        result[write_index++] = '\t';
        break;
      case 'u': {
        unsigned int codepoint;
        size_t digit;

        codepoint = 0U;
        if (read_index + 4U >= size) {
          free(result);
          result = NULL;
          goto done;
        }
        for (digit = 0U; digit < 4U; digit++) {
          int value;

          value = json_hex_value(source[read_index + digit + 1U]);
          if (value < 0) {
            free(result);
            result = NULL;
            goto done;
          }
          codepoint = (codepoint << 4U) | (unsigned int)value;
        }
        read_index += 4U;
        if (codepoint <= 0x7fU) {
          result[write_index++] = (char)codepoint;
        } else if (codepoint <= 0x7ffU) {
          result[write_index++] = (char)(0xc0U | (codepoint >> 6U));
          result[write_index++] = (char)(0x80U | (codepoint & 0x3fU));
        } else {
          result[write_index++] = (char)(0xe0U | (codepoint >> 12U));
          result[write_index++] = (char)(0x80U | ((codepoint >> 6U) & 0x3fU));
          result[write_index++] = (char)(0x80U | (codepoint & 0x3fU));
        }
        break;
      }
      default:
        free(result);
        result = NULL;
        goto done;
      }
    } else {
      result[write_index++] = source[read_index];
    }
  }
  result[write_index] = '\0';

done:
  return result;
}

static int json_hex_value(char value) {
  int result;

  result = -1;
  if (value >= '0' && value <= '9') {
    result = value - '0';
  } else if (value >= 'a' && value <= 'f') {
    result = value - 'a' + 10;
  } else if (value >= 'A' && value <= 'F') {
    result = value - 'A' + 10;
  }
  return result;
}

static bool json_write_string(FILE *stream, const char *value) {
  const unsigned char *cursor;
  bool success;

  success = fputc('"', stream) != EOF;
  for (cursor = (const unsigned char *)value; success && *cursor != '\0';
       cursor++) {
    switch (*cursor) {
    case '"':
      success = fputs("\\\"", stream) >= 0;
      break;
    case '\\':
      success = fputs("\\\\", stream) >= 0;
      break;
    case '\b':
      success = fputs("\\b", stream) >= 0;
      break;
    case '\f':
      success = fputs("\\f", stream) >= 0;
      break;
    case '\n':
      success = fputs("\\n", stream) >= 0;
      break;
    case '\r':
      success = fputs("\\r", stream) >= 0;
      break;
    case '\t':
      success = fputs("\\t", stream) >= 0;
      break;
    default:
      if (*cursor < 0x20U) {
        success = fprintf(stream, "\\u%04x", (unsigned int)*cursor) >= 0;
      } else {
        success = fputc((int)*cursor, stream) != EOF;
      }
      break;
    }
  }
  if (success) {
    success = fputc('"', stream) != EOF;
  }
  return success;
}

static void compile_database_destroy(struct compile_database *database) {
  size_t index;

  for (index = 0U; index < database->count; index++) {
    free(database->entries[index].directory);
    free(database->entries[index].file);
    free(database->entries[index].output);
    string_list_destroy(&database->entries[index].arguments);
  }
  free(database->entries);
  memset(database, 0, sizeof(*database));
}

static bool compile_database_load(const char *path,
                                  struct compile_database *database) {
  struct json_document document;
  size_t index;
  size_t token;
  bool loaded;
  bool success;

  json_document_init(&document);
  success = false;
  loaded = json_document_load(path, &document);
  if (!loaded || document.tokens[0].kind != JSON_ARRAY) {
    goto done;
  }
  database->count = document.tokens[0].children;
  database->entries = calloc(database->count, sizeof(*database->entries));
  if (database->count > 0U && database->entries == NULL) {
    goto done;
  }
  for (index = 0U; index < database->count; index++) {
    if (!json_array_get(&document, 0U, index, &token) ||
        !compile_entry_load(&document, token, &database->entries[index])) {
      goto done;
    }
  }
  success = true;

done:
  if (!success) {
    compile_database_destroy(database);
  }
  json_document_destroy(&document);
  return success;
}

static bool compile_entry_load(const struct json_document *document,
                               size_t object, struct compile_entry *entry) {
  size_t token;
  size_t argument_token;
  size_t index;
  char *argument;
  char *command;
  bool found;
  bool success;

  success = false;
  found = json_object_get(document, object, "directory", &token);
  if (found) {
    entry->directory = json_token_string(document, token);
  }
  found = json_object_get(document, object, "file", &token);
  if (found) {
    entry->file = json_token_string(document, token);
  }
  found = json_object_get(document, object, "output", &token);
  if (found) {
    entry->output = json_token_string(document, token);
  }
  if (entry->directory == NULL) {
    entry->directory = duplicate_text(".");
  }
  if (entry->file == NULL || entry->directory == NULL) {
    goto done;
  }
  found = json_object_get(document, object, "arguments", &token);
  if (found && document->tokens[token].kind == JSON_ARRAY) {
    for (index = 0U; index < document->tokens[token].children; index++) {
      found = json_array_get(document, token, index, &argument_token);
      if (!found) {
        goto done;
      }
      argument = json_token_string(document, argument_token);
      if (argument == NULL || !string_list_add(&entry->arguments, argument)) {
        goto done;
      }
    }
  } else {
    found = json_object_get(document, object, "command", &token);
    if (!found) {
      goto done;
    }
    command = json_token_string(document, token);
    if (command == NULL) {
      goto done;
    }
    success = shell_split(command, &entry->arguments);
    free(command);
    if (!success) {
      goto done;
    }
  }
  success = entry->arguments.count > 0U;

done:
  return success;
}

static bool shell_split(const char *command, struct string_list *arguments) {
  const char *cursor;
  char *word;
  size_t length;
  size_t capacity;
  char quote;
  bool escaped;
  bool started;
  bool appended;
  bool success;

  cursor = command;
  success = true;
  while (*cursor != '\0' && success) {
    while (isspace((unsigned char)*cursor)) {
      cursor++;
    }
    if (*cursor == '\0') {
      break;
    }
    word = NULL;
    length = 0U;
    capacity = 0U;
    quote = '\0';
    escaped = false;
    started = false;
    while (*cursor != '\0') {
      if (escaped) {
        appended = append_shell_character(&word, &length, &capacity, *cursor);
        escaped = false;
        started = true;
        cursor++;
        if (!appended) {
          success = false;
          break;
        }
        continue;
      }
      if (quote != '\'' && *cursor == '\\') {
        escaped = true;
        cursor++;
        continue;
      }
      if (quote == '\0' && (*cursor == '\'' || *cursor == '"')) {
        quote = *cursor;
        started = true;
        cursor++;
        continue;
      }
      if (quote != '\0' && *cursor == quote) {
        quote = '\0';
        cursor++;
        continue;
      }
      if (quote == '\0' && isspace((unsigned char)*cursor)) {
        break;
      }
      appended = append_shell_character(&word, &length, &capacity, *cursor);
      started = true;
      cursor++;
      if (!appended) {
        success = false;
        break;
      }
    }
    if (escaped || quote != '\0') {
      success = false;
    }
    if (success && started) {
      appended = append_shell_character(&word, &length, &capacity, '\0');
      success = appended && string_list_add(arguments, word);
      if (success) {
        word = NULL;
      }
    }
    free(word);
  }
  if (!success) {
    fprintf(stderr, "cannot parse compile command shell quoting\n");
  }
  return success;
}

static bool append_shell_character(char **word, size_t *length,
                                   size_t *capacity, char value) {
  char *resized;
  size_t next_capacity;
  bool success;

  success = false;
  if (*length == *capacity) {
    next_capacity = *capacity == 0U ? 64U : *capacity * 2U;
    if (next_capacity < *capacity) {
      goto done;
    }
    resized = realloc(*word, next_capacity);
    if (resized == NULL) {
      goto done;
    }
    *word = resized;
    *capacity = next_capacity;
  }
  (*word)[*length] = value;
  (*length)++;
  success = true;

done:
  return success;
}

static bool sanitize_arguments(const struct string_list *input,
                               struct string_list *output) {
  size_t index;
  char *copy;
  bool success;

  success = true;
  for (index = 0U; index < input->count && success; index++) {
    if (sanitizer_should_drop(input->items[index])) {
      continue;
    }
    copy = duplicate_text(input->items[index]);
    success = copy != NULL && string_list_add(output, copy);
  }
  return success;
}

static bool sanitizer_should_drop(const char *argument) {
  bool drop;

  drop = strcmp(argument, "--coverage") == 0 ||
         strcmp(argument, "-coverage") == 0 || strcmp(argument, "-pg") == 0 ||
         strcmp(argument, "-p") == 0 || starts_with(argument, "-W") ||
         starts_with(argument, "-g") ||
         (starts_with(argument, "-f") && !sanitizer_keep_f_option(argument));
  return drop;
}

static bool sanitizer_keep_f_option(const char *argument) {
  static const char *const prefixes[] = {"-fPIC",
                                         "-fpic",
                                         "-fPIE",
                                         "-fpie",
                                         "-fexceptions",
                                         "-fno-exceptions",
                                         "-frtti",
                                         "-fno-rtti",
                                         "-fvisibility",
                                         "-fsigned-char",
                                         "-funsigned-char",
                                         "-fshort-enums",
                                         "-fno-short-enums",
                                         "-fshort-wchar",
                                         "-ffreestanding",
                                         "-fno-builtin",
                                         "-fwrapv",
                                         "-fno-wrapv",
                                         "-fstrict-aliasing",
                                         "-fno-strict-aliasing",
                                         "-fdelete-null-pointer-checks",
                                         "-fno-delete-null-pointer-checks",
                                         "-fopenmp"};
  size_t index;
  bool keep;

  keep = false;
  for (index = 0U; index < sizeof(prefixes) / sizeof(prefixes[0]); index++) {
    if (starts_with(argument, prefixes[index])) {
      keep = true;
      break;
    }
  }
  return keep;
}

static bool write_sanitized_database(const char *path,
                                     const struct compile_database *database) {
  struct string_list sanitized;
  FILE *stream;
  size_t index;
  size_t argument;
  bool success;

  memset(&sanitized, 0, sizeof(sanitized));
  success = false;
  stream = fopen(path, "w");
  if (stream == NULL) {
    fprintf(stderr, "cannot create %s: %s\n", path, strerror(errno));
    goto done;
  }
  if (fputs("[\n", stream) < 0) {
    goto done;
  }
  for (index = 0U; index < database->count; index++) {
    if (!sanitize_arguments(&database->entries[index].arguments, &sanitized)) {
      goto done;
    }
    if (fputs(index == 0U ? "  {\n" : ",\n  {\n", stream) < 0 ||
        fputs("    \"directory\": ", stream) < 0 ||
        !json_write_string(stream, database->entries[index].directory) ||
        fputs(",\n    \"file\": ", stream) < 0 ||
        !json_write_string(stream, database->entries[index].file)) {
      goto done;
    }
    if (database->entries[index].output != NULL) {
      if (fputs(",\n    \"output\": ", stream) < 0 ||
          !json_write_string(stream, database->entries[index].output)) {
        goto done;
      }
    }
    if (fputs(",\n    \"arguments\": [", stream) < 0) {
      goto done;
    }
    for (argument = 0U; argument < sanitized.count; argument++) {
      if (fputs(argument == 0U ? "\n      " : ",\n      ", stream) < 0 ||
          !json_write_string(stream, sanitized.items[argument])) {
        goto done;
      }
    }
    if ((sanitized.count > 0U && fputs("\n    ]\n  }", stream) < 0) ||
        (sanitized.count == 0U && fputs("]\n  }", stream) < 0)) {
      goto done;
    }
    string_list_destroy(&sanitized);
  }
  success = fputs("\n]\n", stream) >= 0 && fclose(stream) == 0;
  stream = NULL;

done:
  if (stream != NULL) {
    fclose(stream);
  }
  string_list_destroy(&sanitized);
  return success;
}

static bool source_file(const char *path) {
  const char *extension;
  bool result;

  extension = strrchr(path, '.');
  result =
      extension != NULL &&
      (strcasecmp(extension, ".c") == 0 || strcasecmp(extension, ".cc") == 0 ||
       strcasecmp(extension, ".cpp") == 0 ||
       strcasecmp(extension, ".cxx") == 0 || strcasecmp(extension, ".m") == 0 ||
       strcasecmp(extension, ".mm") == 0);
  return result;
}

static bool strip_output_arguments(const struct string_list *input,
                                   struct string_list *output) {
  size_t index;
  char *copy;
  bool skip;
  bool success;

  skip = false;
  success = true;
  for (index = 0U; index < input->count && success; index++) {
    if (skip) {
      skip = false;
      continue;
    }
    if (strcmp(input->items[index], "-c") == 0) {
      continue;
    }
    if (strcmp(input->items[index], "-o") == 0) {
      skip = true;
      continue;
    }
    if (starts_with(input->items[index], "-o") &&
        strlen(input->items[index]) > 2U) {
      continue;
    }
    copy = duplicate_text(input->items[index]);
    success = copy != NULL && string_list_add(output, copy);
  }
  return success;
}

static char *analysis_log_path(const char *directory, const char *source) {
  char *flat;
  char *path;
  size_t size;

  flat = replace_separators(source);
  path = NULL;
  if (flat != NULL) {
    size = strlen(directory) + strlen(flat) + sizeof("/.txt");
    path = malloc(size);
    if (path != NULL) {
      snprintf(path, size, "%s/%s.txt", directory, flat);
    }
  }
  free(flat);
  return path;
}

static bool run_child(char *const argv[], const char *directory,
                      unsigned int timeout_seconds,
                      struct child_result *result) {
  int pipe_fds[2];
  int flags;
  int wait_status;
  int wait_result;
  pid_t process;
  struct timespec start;
  struct timespec now;
  struct timespec pause_time;
  char buffer[4096];
  ssize_t count;
  bool running;
  bool success;

  child_result_destroy(result);
  success = false;
  if (pipe(pipe_fds) != 0) {
    goto done;
  }
  process = fork();
  if (process < 0) {
    close(pipe_fds[0]);
    close(pipe_fds[1]);
    goto done;
  }
  if (process == 0) {
    close(pipe_fds[0]);
    dup2(pipe_fds[1], STDOUT_FILENO);
    dup2(pipe_fds[1], STDERR_FILENO);
    close(pipe_fds[1]);
    if (directory != NULL && chdir(directory) != 0) {
      _exit(126);
    }
    execvp(argv[0], argv);
    _exit(127);
  }
  close(pipe_fds[1]);
  flags = fcntl(pipe_fds[0], F_GETFL, 0);
  if (flags >= 0) {
    fcntl(pipe_fds[0], F_SETFL, flags | O_NONBLOCK);
  }
  clock_gettime(CLOCK_MONOTONIC, &start);
  pause_time.tv_sec = 0;
  pause_time.tv_nsec = 10000000L;
  running = true;
  wait_status = 0;
  while (running) {
    do {
      count = read(pipe_fds[0], buffer, sizeof(buffer));
      if (count > 0 && !child_output_append(result, buffer, (size_t)count)) {
        kill(process, SIGKILL);
        waitpid(process, &wait_status, 0);
        close(pipe_fds[0]);
        goto done;
      }
    } while (count > 0);
    wait_result = waitpid(process, &wait_status, WNOHANG);
    if (wait_result == process) {
      running = false;
    } else if (wait_result < 0) {
      kill(process, SIGKILL);
      waitpid(process, &wait_status, 0);
      running = false;
    } else {
      clock_gettime(CLOCK_MONOTONIC, &now);
      if ((unsigned int)(now.tv_sec - start.tv_sec) >= timeout_seconds) {
        result->timed_out = true;
        kill(process, SIGKILL);
        waitpid(process, &wait_status, 0);
        running = false;
      } else {
        nanosleep(&pause_time, NULL);
      }
    }
  }
  do {
    count = read(pipe_fds[0], buffer, sizeof(buffer));
    if (count > 0 && !child_output_append(result, buffer, (size_t)count)) {
      close(pipe_fds[0]);
      goto done;
    }
  } while (count > 0);
  close(pipe_fds[0]);
  result->status = result->timed_out ? 2 : child_exit_status(wait_status);
  success = true;

done:
  return success;
}

static void child_result_destroy(struct child_result *result) {
  free(result->output);
  memset(result, 0, sizeof(*result));
}

static bool child_output_append(struct child_result *result, const char *data,
                                size_t size) {
  char *output;
  bool success;

  success = false;
  if (size > SIZE_MAX - result->output_size - 1U) {
    goto done;
  }
  output = realloc(result->output, result->output_size + size + 1U);
  if (output == NULL) {
    goto done;
  }
  result->output = output;
  memcpy(result->output + result->output_size, data, size);
  result->output_size += size;
  result->output[result->output_size] = '\0';
  success = true;

done:
  return success;
}

static int child_exit_status(int wait_status) {
  int status;

  status = 2;
  if (WIFEXITED(wait_status)) {
    status = WEXITSTATUS(wait_status);
  } else if (WIFSIGNALED(wait_status)) {
    status = 128 + WTERMSIG(wait_status);
  }
  return status;
}

static bool arguments_to_vector(const struct string_list *arguments,
                                char ***vector) {
  char **items;
  size_t index;
  bool success;

  success = false;
  items = calloc(arguments->count + 1U, sizeof(*items));
  if (items == NULL) {
    goto done;
  }
  for (index = 0U; index < arguments->count; index++) {
    items[index] = duplicate_text(arguments->items[index]);
    if (items[index] == NULL) {
      vector_destroy(items);
      items = NULL;
      goto done;
    }
  }
  *vector = items;
  success = true;

done:
  return success;
}

static void vector_destroy(char **vector) {
  size_t index;

  if (vector != NULL) {
    for (index = 0U; vector[index] != NULL; index++) {
      free(vector[index]);
    }
    free(vector);
  }
}

static bool path_is_within(const char *root, const char *path) {
  size_t root_size;
  bool inside;

  root_size = strlen(root);
  inside = strncmp(root, path, root_size) == 0 &&
           (path[root_size] == '/' || path[root_size] == '\0');
  return inside;
}

static char *absolute_path(const char *directory, const char *path) {
  char *joined;
  char *resolved;
  size_t size;

  joined = NULL;
  if (path[0] == '/') {
    joined = duplicate_text(path);
  } else {
    size = strlen(directory) + strlen(path) + 2U;
    joined = malloc(size);
    if (joined != NULL) {
      snprintf(joined, size, "%s/%s", directory, path);
    }
  }
  resolved = NULL;
  if (joined != NULL) {
    resolved = realpath(joined, NULL);
  }
  free(joined);
  return resolved;
}

static char *replace_separators(const char *path) {
  char *result;
  size_t index;

  result = duplicate_text(path);
  if (result != NULL) {
    for (index = 0U; result[index] != '\0'; index++) {
      if (result[index] == '/' || result[index] == '\\') {
        result[index] = '_';
      }
    }
  }
  return result;
}

static bool ctu_extdef_arguments(const struct string_list *input,
                                 struct string_list *output) {
  static const char *const pair_options[] = {
      "-I",       "-F",        "-D",         "-U",      "-arch",   "-target",
      "-isystem", "-isysroot", "-idirafter", "-iquote", "-include"};
  static const char *const prefixes[] = {
      "-I",       "-F",       "-D",        "-U",         "-std=",   "-arch=",
      "-target=", "-isystem", "-isysroot", "-idirafter", "-iquote", "-include"};
  size_t index;
  size_t option;
  char *copy;
  bool pair;
  bool keep;
  bool success;

  success = true;
  for (index = 1U; index < input->count && success; index++) {
    pair = false;
    for (option = 0U; option < sizeof(pair_options) / sizeof(pair_options[0]);
         option++) {
      if (strcmp(input->items[index], pair_options[option]) == 0) {
        pair = true;
        break;
      }
    }
    if (pair) {
      copy = duplicate_text(input->items[index]);
      success = copy != NULL && string_list_add(output, copy);
      if (success && index + 1U < input->count) {
        index++;
        copy = duplicate_text(input->items[index]);
        success = copy != NULL && string_list_add(output, copy);
      }
      continue;
    }
    keep = false;
    for (option = 0U; option < sizeof(prefixes) / sizeof(prefixes[0]);
         option++) {
      if (starts_with(input->items[index], prefixes[option])) {
        keep = true;
        break;
      }
    }
    if (keep) {
      copy = duplicate_text(input->items[index]);
      success = copy != NULL && string_list_add(output, copy);
    }
  }
  return success;
}

static bool ctu_analysis_arguments(const struct string_list *input,
                                   struct string_list *output) {
  static const char *const drops[] = {
      "-fsanitize", "-fno-sanitize", "-fprofile",    "-fcoverage",
      "--coverage", "-fharden",      "-finstrument", "-pg"};
  struct string_list stripped;
  size_t index;
  size_t drop;
  char *copy;
  bool rejected;
  bool success;

  memset(&stripped, 0, sizeof(stripped));
  success = strip_output_arguments(input, &stripped);
  for (index = 1U; index < stripped.count && success; index++) {
    rejected = strcmp(stripped.items[index], "-p") == 0;
    for (drop = 0U; drop < sizeof(drops) / sizeof(drops[0]) && !rejected;
         drop++) {
      rejected = starts_with(stripped.items[index], drops[drop]);
    }
    if (!rejected) {
      copy = duplicate_text(stripped.items[index]);
      success = copy != NULL && string_list_add(output, copy);
    }
  }
  string_list_destroy(&stripped);
  return success;
}

static bool path_parent_directories(const char *path) {
  char *copy;
  char *separator;
  bool success;

  copy = duplicate_text(path);
  success = copy != NULL;
  if (!success) {
    goto done;
  }
  separator = strrchr(copy, '/');
  if (separator != NULL) {
    *separator = '\0';
    if (copy[0] != '\0') {
      success = make_directories(copy);
    }
  }

done:
  free(copy);
  return success;
}

static bool regular_file(const char *path) {
  struct stat status_buffer;
  bool result;

  result = stat(path, &status_buffer) == 0 && S_ISREG(status_buffer.st_mode);
  return result;
}

static bool format_source_name(const char *path) {
  static const char *const extensions[] = {".c", ".cc", ".cpp", ".cxx",
                                           ".h", ".hh", ".hpp", ".hxx"};
  const char *extension;
  size_t index;
  bool result;

  extension = strrchr(path, '.');
  result = false;
  if (extension != NULL) {
    for (index = 0U; index < sizeof(extensions) / sizeof(extensions[0]);
         index++) {
      if (strcasecmp(extension, extensions[index]) == 0) {
        result = true;
        break;
      }
    }
  }
  return result;
}

static bool vendored_source(const char *relative) {
  bool result;

  result = starts_with(relative, "external/") ||
           starts_with(relative, "third_party/") ||
           starts_with(relative, "vendor/") ||
           starts_with(relative, "vendored/") ||
           starts_with(relative, "test/unity/");
  return result;
}

static bool collect_repositories(const char *scripts_root,
                                 struct string_list *repositories) {
  char *manifest_path;
  char *manifest;
  char *line;
  char *next;
  char *comment;
  char *first;
  char *second;
  char *candidate;
  char *resolved;
  size_t manifest_size;
  size_t candidate_size;
  bool read_ok;
  bool added;
  bool success;

  manifest_path = NULL;
  manifest = NULL;
  resolved = realpath(scripts_root, NULL);
  if (resolved == NULL) {
    success = false;
    goto done;
  }
  success = string_list_add(repositories, resolved);
  resolved = NULL;
  if (!success) {
    goto done;
  }
  manifest_path = malloc(strlen(scripts_root) + sizeof("/repos.txt"));
  if (manifest_path == NULL) {
    success = false;
    goto done;
  }
  sprintf(manifest_path, "%s/repos.txt", scripts_root);
  read_ok = read_file(manifest_path, &manifest, &manifest_size);
  if (!read_ok) {
    success = false;
    goto done;
  }
  line = manifest;
  while (*line != '\0' && success) {
    next = strchr(line, '\n');
    if (next != NULL) {
      *next = '\0';
    }
    comment = strchr(line, '#');
    if (comment != NULL) {
      *comment = '\0';
    }
    first = strchr(line, '|');
    second = first == NULL ? NULL : strchr(first + 1, '|');
    if (first != NULL && second != NULL && second > first + 1) {
      *second = '\0';
      candidate_size = strlen(scripts_root) + strlen(first + 1) + 2U;
      candidate = malloc(candidate_size);
      if (candidate == NULL) {
        success = false;
        break;
      }
      snprintf(candidate, candidate_size, "%s/%s", scripts_root, first + 1);
      resolved = realpath(candidate, NULL);
      free(candidate);
      if (resolved != NULL) {
        added = string_list_add(repositories, resolved);
        resolved = NULL;
        if (!added) {
          success = false;
          break;
        }
      }
    }
    line = next == NULL ? line + strlen(line) : next + 1;
  }

done:
  free(resolved);
  free(manifest_path);
  free(manifest);
  return success;
}

static bool collect_tracked_sources(const struct string_list *repositories,
                                    struct string_list *sources,
                                    struct string_list *excluded) {
  struct child_result child;
  char *command[] = {"git", "-C", NULL, "ls-files", "-z", NULL};
  char *relative;
  char *absolute;
  size_t repository_index;
  size_t cursor;
  size_t end;
  size_t absolute_size;
  bool ran;
  bool added;
  bool success;

  memset(&child, 0, sizeof(child));
  success = true;
  for (repository_index = 0U; repository_index < repositories->count && success;
       repository_index++) {
    command[2] = repositories->items[repository_index];
    ran = run_child(command, NULL, 120U, &child);
    if (!ran || child.status != 0) {
      fprintf(stderr, "cannot inventory tracked sources in %s\n",
              repositories->items[repository_index]);
      if (child.output != NULL) {
        fputs(child.output, stderr);
      }
      success = false;
      break;
    }
    cursor = 0U;
    while (cursor < child.output_size && success) {
      end = cursor;
      while (end < child.output_size && child.output[end] != '\0') {
        end++;
      }
      if (end == cursor) {
        cursor++;
        continue;
      }
      relative = duplicate_range(child.output + cursor, end - cursor);
      if (relative == NULL) {
        success = false;
        break;
      }
      if (format_source_name(relative)) {
        absolute_size = strlen(repositories->items[repository_index]) +
                        strlen(relative) + 2U;
        absolute = malloc(absolute_size);
        if (absolute == NULL) {
          free(relative);
          success = false;
          break;
        }
        snprintf(absolute, absolute_size, "%s/%s",
                 repositories->items[repository_index], relative);
        if (regular_file(absolute)) {
          if (vendored_source(relative)) {
            added = string_list_add(excluded, absolute);
          } else {
            added = string_list_add(sources, absolute);
          }
          if (!added) {
            free(relative);
            success = false;
            break;
          }
        } else {
          free(absolute);
        }
      }
      free(relative);
      cursor = end + 1U;
    }
    child_result_destroy(&child);
  }
  child_result_destroy(&child);
  return success;
}

static uint64_t file_fingerprint(const char *path, bool *read_ok) {
  unsigned char buffer[16384];
  FILE *stream;
  size_t amount;
  size_t index;
  uint64_t value;

  value = UINT64_C(14695981039346656037);
  *read_ok = false;
  stream = fopen(path, "rb");
  if (stream == NULL) {
    goto done;
  }
  while ((amount = fread(buffer, 1U, sizeof(buffer), stream)) > 0U) {
    for (index = 0U; index < amount; index++) {
      value ^= buffer[index];
      value *= UINT64_C(1099511628211);
    }
  }
  *read_ok = ferror(stream) == 0 && fclose(stream) == 0;
  stream = NULL;

done:
  if (stream != NULL) {
    fclose(stream);
  }
  return value;
}

static bool write_format_path_array(FILE *stream,
                                    const struct string_list *paths,
                                    const char *workspace) {
  const char *relative;
  size_t workspace_size;
  size_t index;
  bool written;

  workspace_size = strlen(workspace);
  written = fputc('[', stream) != EOF;
  for (index = 0U; index < paths->count && written; index++) {
    relative = starts_with(paths->items[index], workspace) &&
                       paths->items[index][workspace_size] == '/'
                   ? paths->items[index] + workspace_size + 1U
                   : paths->items[index];
    if (index > 0U) {
      written = fputc(',', stream) != EOF;
    }
    if (written) {
      written = json_write_string(stream, relative);
    }
  }
  if (written) {
    written = fputc(']', stream) != EOF;
  }
  return written;
}

static bool write_format_receipt(const char *path, const char *formatter,
                                 const char *version, bool check,
                                 const struct string_list *sources,
                                 const struct string_list *excluded,
                                 const struct string_list *changed,
                                 const char *workspace, bool passed) {
  FILE *stream;
  bool directories_ready;
  bool written;

  stream = NULL;
  written = false;
  directories_ready = path_parent_directories(path);
  if (!directories_ready) {
    goto done;
  }
  stream = fopen(path, "w");
  if (stream == NULL) {
    fprintf(stderr, "cannot create %s: %s\n", path, strerror(errno));
    goto done;
  }
  written = fputs("{\n  \"schema\": \"p101-format-workspace-receipt-v1\",\n  "
                  "\"formatter\": ",
                  stream) >= 0;
  written = written && json_write_string(stream, formatter);
  written = written && fputs(",\n  \"formatter_version\": ", stream) >= 0;
  written = written && json_write_string(stream, version);
  written = written && fputs(",\n  \"mode\": ", stream) >= 0;
  written = written && json_write_string(stream, check ? "check" : "apply");
  written = written && fprintf(stream,
                               ",\n  \"source_count\": %zu,\n  "
                               "\"excluded_vendored_count\": %zu,\n  "
                               "\"excluded_vendored\": ",
                               sources->count, excluded->count) >= 0;
  written = written && write_format_path_array(stream, excluded, workspace);
  written = written &&
            fprintf(stream, ",\n  \"changed_count\": %zu,\n  \"changed\": ",
                    changed->count) >= 0;
  written = written && write_format_path_array(stream, changed, workspace);
  written = written &&
            fprintf(stream,
                    ",\n  \"passed\": %s,\n  \"does_not_prove\": "
                    "\"A clean formatting pass proves only that tracked "
                    "C/C++ source bytes match the recorded clang-format "
                    "version and repository style files. A different version "
                    "may format the same sources differently.\"\n}\n",
                    passed ? "true" : "false") >= 0;
  if (fclose(stream) != 0) {
    written = false;
  }
  stream = NULL;

done:
  if (stream != NULL) {
    fclose(stream);
  }
  return stream == NULL && directories_ready && written;
}

static bool render_flag_selection(const char *selection_path,
                                  const char *output_directory, bool check,
                                  bool *in_sync) {
  struct json_document document;
  struct string_list overrides[4];
  struct string_list selected;
  size_t files;
  size_t index;
  size_t child;
  size_t file_count;
  size_t entry_count;
  bool loaded;
  bool success;

  json_document_init(&document);
  memset(overrides, 0, sizeof(overrides));
  memset(&selected, 0, sizeof(selected));
  *in_sync = true;
  file_count = 0U;
  entry_count = 0U;
  loaded = json_document_load(selection_path, &document);
  success = loaded && document.count > 0U &&
            document.tokens[0].kind == JSON_OBJECT &&
            json_object_get(&document, 0U, "files", &files) &&
            document.tokens[files].kind == JSON_OBJECT;
  if (!success) {
    fprintf(stderr, "p101-bootstrap: invalid flag selection: %s\n",
            selection_path);
    goto done;
  }
  if (!check && !make_directories(output_directory)) {
    success = false;
    goto done;
  }
  child = 0U;
  for (index = files + 1U;
       success && index < document.count &&
       document.tokens[index].start < document.tokens[files].end;
       index++) {
    char *name;
    char *path;
    char *rendered;
    size_t rendered_size;

    if (document.tokens[index].parent != files) {
      continue;
    }
    if ((child % 2U) != 0U) {
      child++;
      continue;
    }
    if (index + 1U >= document.count ||
        document.tokens[index + 1U].parent != files ||
        document.tokens[index + 1U].kind != JSON_OBJECT) {
      success = false;
      break;
    }
    name = json_token_string(&document, index);
    path = NULL;
    rendered = NULL;
    rendered_size = 0U;
    success =
        name != NULL && strchr(name, '/') == NULL && strchr(name, '\\') == NULL;
    if (success) {
      path = joined_path(output_directory, name);
      success =
          path != NULL && string_list_add(&selected, duplicate_text(name));
    }
    if (success) {
      success = render_flag_file(&document, index + 1U, &rendered,
                                 &rendered_size, overrides, &entry_count);
    }
    if (success && check) {
      char *actual;
      char *expected_units;
      char *actual_units;
      size_t actual_size;

      actual = NULL;
      actual_size = 0U;
      expected_units = normalize_active_flag_units(rendered);
      loaded = read_file(path, &actual, &actual_size);
      actual_units = loaded ? normalize_active_flag_units(actual) : NULL;
      if (expected_units == NULL || actual_units == NULL ||
          strcmp(expected_units, actual_units) != 0) {
        fprintf(stderr, "OUT OF SYNC: %s\n", name);
        *in_sync = false;
      }
      free(actual_units);
      free(expected_units);
      free(actual);
    } else if (success) {
      success = write_file(path, rendered, rendered_size);
    }
    free(rendered);
    free(path);
    free(name);
    file_count++;
    child += 2U;
    index++;
  }
  if (success && (file_count == 0U || entry_count == 0U)) {
    fprintf(stderr, "p101-bootstrap: flag selection contains no entries\n");
    success = false;
  }
  if (success && !check) {
    success = write_flag_overrides(output_directory, overrides);
  }
  if (success && !check) {
    success = stub_stale_flag_files(output_directory, &selected);
  }
  if (success) {
    if (check && *in_sync) {
      printf("flag selection and rendered flags are in sync.\n");
    } else if (!check) {
      printf("rendered %zu files (%zu entries); overrides: gcc=%zu "
             "clang=%zu c=%zu cxx=%zu\n",
             file_count, entry_count, overrides[0].count, overrides[1].count,
             overrides[2].count, overrides[3].count);
    }
  }

done:
  for (index = 0U; index < 4U; index++) {
    string_list_destroy(&overrides[index]);
  }
  string_list_destroy(&selected);
  json_document_destroy(&document);
  return success;
}

static bool render_flag_file(const struct json_document *document,
                             size_t specification, char **output,
                             size_t *output_size,
                             struct string_list overrides[4],
                             size_t *entry_count) {
  static const char *const families[] = {"gcc", "clang", "c", "cxx"};
  size_t header;
  size_t entries;
  size_t index;
  FILE *stream;
  bool success;

  *output = NULL;
  *output_size = 0U;
  stream = open_memstream(output, output_size);
  success = stream != NULL;
  if (!success) {
    goto done;
  }
  if (json_object_get(document, specification, "header", &header) &&
      document->tokens[header].kind == JSON_ARRAY) {
    for (index = 0U; success && index < document->tokens[header].children;
         index++) {
      size_t token;
      char *text;

      text = NULL;
      success = json_array_get(document, header, index, &token);
      if (success) {
        text = json_token_string(document, token);
        success = text != NULL;
      }
      if (success) {
        success =
            fprintf(stream, "%s%s\n", text[0] == '\0' ? "#" : "# ", text) >= 0;
      }
      free(text);
    }
    if (success && document->tokens[header].children > 0U) {
      success = fputc('\n', stream) != EOF;
    }
  }
  if (!json_object_get(document, specification, "entries", &entries) ||
      document->tokens[entries].kind != JSON_ARRAY) {
    success = false;
    goto close;
  }
  for (index = 0U; success && index < document->tokens[entries].children;
       index++) {
    size_t entry;
    size_t flags_token;
    size_t comment_token;
    char *flags;
    char *comment;
    bool enabled;
    struct string_list disabled;
    size_t family;

    flags = NULL;
    comment = NULL;
    memset(&disabled, 0, sizeof(disabled));
    success = json_array_get(document, entries, index, &entry) &&
              document->tokens[entry].kind == JSON_OBJECT &&
              json_object_get(document, entry, "flags", &flags_token);
    if (success) {
      flags = json_token_string(document, flags_token);
      success = flags != NULL;
    }
    enabled = success && flag_entry_enabled(document, entry);
    if (success &&
        json_object_get(document, entry, "comment", &comment_token)) {
      comment = json_token_string(document, comment_token);
      success = comment != NULL;
    }
    for (family = 0U; success && family < 4U; family++) {
      size_t family_token;

      if (enabled &&
          json_object_get(document, entry, families[family], &family_token) &&
          json_token_is_false(document, family_token)) {
        success =
            string_list_add(&disabled, duplicate_text(families[family])) &&
            append_flag_override_tokens(&overrides[family], flags);
      }
    }
    if (success) {
      success =
          fprintf(stream, "%s        \"%s\"", enabled ? "" : "#", flags) >= 0;
    }
    if (success && disabled.count > 0U) {
      success = fputs("    # [", stream) >= 0;
      for (family = 0U; success && family < disabled.count; family++) {
        success = fprintf(stream, "%s%s", family == 0U ? "" : "/",
                          disabled.items[family]) >= 0;
      }
      if (success) {
        success = fprintf(stream, " off via overrides] %s",
                          comment == NULL ? "" : comment) >= 0;
      }
    } else if (success && comment != NULL && comment[0] != '\0') {
      success = fprintf(stream, "    # %s", comment) >= 0;
    }
    if (success) {
      success = fputc('\n', stream) != EOF;
    }
    string_list_destroy(&disabled);
    free(comment);
    free(flags);
    (*entry_count)++;
  }

close:
  if (fclose(stream) != 0) {
    success = false;
  }
  stream = NULL;

done:
  if (stream != NULL) {
    fclose(stream);
  }
  if (!success) {
    free(*output);
    *output = NULL;
    *output_size = 0U;
  }
  return success;
}

static bool flag_entry_enabled(const struct json_document *document,
                               size_t entry) {
  size_t enabled;
  bool result;

  result = json_object_get(document, entry, "enabled", &enabled) &&
           json_token_equals(document, enabled, "true");
  return result;
}

static bool append_flag_override_tokens(struct string_list *list,
                                        const char *flags) {
  char *copy;
  char *save;
  char *token;
  bool success;

  copy = duplicate_text(flags);
  save = NULL;
  success = copy != NULL;
  token = success ? strtok_r(copy, " \t\r\n", &save) : NULL;
  while (success && token != NULL) {
    success = string_list_add(list, duplicate_text(token));
    token = strtok_r(NULL, " \t\r\n", &save);
  }
  free(copy);
  return success;
}

static char *normalize_active_flag_units(const char *text) {
  size_t length;
  char *output;
  size_t read_index;
  size_t write_index;
  bool line_has_text;
  bool pending_space;
  bool comment;

  length = strlen(text);
  output = malloc(length + 1U);
  if (output == NULL) {
    goto done;
  }
  write_index = 0U;
  line_has_text = false;
  pending_space = false;
  comment = false;
  for (read_index = 0U; read_index <= length; read_index++) {
    char value;

    value = text[read_index];
    if (value == '\n' || value == '\0') {
      if (line_has_text) {
        output[write_index++] = '\n';
      }
      line_has_text = false;
      pending_space = false;
      comment = false;
      continue;
    }
    if (comment) {
      continue;
    }
    if (value == '#') {
      comment = true;
      continue;
    }
    if (value == '"' || isspace((unsigned char)value)) {
      if (line_has_text) {
        pending_space = true;
      }
      continue;
    }
    if (pending_space) {
      output[write_index++] = ' ';
      pending_space = false;
    }
    output[write_index++] = value;
    line_has_text = true;
  }
  output[write_index] = '\0';

done:
  return output;
}

static char *joined_path(const char *directory, const char *name) {
  char *path;
  size_t size;

  size = strlen(directory) + strlen(name) + 2U;
  path = malloc(size);
  if (path != NULL) {
    snprintf(path, size, "%s/%s", directory, name);
  }
  return path;
}

static bool string_list_contains(const struct string_list *list,
                                 const char *value) {
  size_t index;
  bool found;

  found = false;
  for (index = 0U; index < list->count; index++) {
    if (strcmp(list->items[index], value) == 0) {
      found = true;
      break;
    }
  }
  return found;
}

static bool write_flag_overrides(const char *directory,
                                 struct string_list overrides[4]) {
  static const char *const families[] = {"gcc", "clang", "c", "cxx"};
  static const char *const scopes[] = {"gcc-family compilers",
                                       "clang-family compilers",
                                       "the C language", "the C++ language"};
  size_t family;
  bool success;

  success = true;
  for (family = 0U; success && family < 4U; family++) {
    char name[64];
    char *path;

    snprintf(name, sizeof(name), "overrides-%s.txt", families[family]);
    path = joined_path(directory, name);
    success = path != NULL;
    if (success && overrides[family].count == 0U) {
      if (unlink(path) != 0 && errno != ENOENT) {
        success = false;
      }
    } else if (success) {
      FILE *stream;
      size_t index;

      stream = fopen(path, "w");
      success = stream != NULL;
      if (success) {
        success = fprintf(stream,
                          "# tokens NOT probed for %s — generated by "
                          "p101-bootstrap from flag-selection.json\n",
                          scopes[family]) >= 0;
      }
      for (index = 0U; success && index < overrides[family].count; index++) {
        success = fprintf(stream, "%s\n", overrides[family].items[index]) >= 0;
      }
      if (stream != NULL && fclose(stream) != 0) {
        success = false;
      }
    }
    free(path);
  }
  return success;
}

static bool stub_stale_flag_files(const char *directory,
                                  const struct string_list *selected) {
  DIR *stream;
  struct dirent *entry;
  bool success;

  stream = opendir(directory);
  success = stream != NULL;
  if (!success) {
    goto done;
  }
  errno = 0;
  entry = readdir(stream);
  while (entry != NULL) {
    size_t length;

    length = strlen(entry->d_name);
    if (length > 4U && strcmp(entry->d_name + length - 4U, ".txt") == 0 &&
        !starts_with(entry->d_name, "overrides-") &&
        !string_list_contains(selected, entry->d_name)) {
      static const char empty[] =
          "# (no entries in flag-selection.json for this file — rendered "
          "empty)\n";
      char *path;

      path = joined_path(directory, entry->d_name);
      success = path != NULL && write_file(path, empty, sizeof(empty) - 1U);
      free(path);
      if (!success) {
        break;
      }
    }
    entry = readdir(stream);
  }
  if (success && errno != 0) {
    success = false;
  }
  if (closedir(stream) != 0) {
    success = false;
  }
  stream = NULL;

done:
  if (stream != NULL) {
    closedir(stream);
  }
  return success;
}

static bool collect_active_flags(const char *directory,
                                 struct flag_occurrence_list *occurrences) {
  struct string_list names;
  DIR *stream;
  struct dirent *entry;
  size_t index;
  bool success;

  memset(&names, 0, sizeof(names));
  stream = opendir(directory);
  success = stream != NULL;
  if (!success) {
    fprintf(stderr, "cannot open %s: %s\n", directory, strerror(errno));
    goto done;
  }
  errno = 0;
  entry = readdir(stream);
  while (success && entry != NULL) {
    size_t length;

    length = strlen(entry->d_name);
    if (length > 4U && strcmp(entry->d_name + length - 4U, ".txt") == 0 &&
        !starts_with(entry->d_name, "overrides-")) {
      success = string_list_add(&names, duplicate_text(entry->d_name));
    }
    entry = readdir(stream);
  }
  if (success && errno != 0) {
    success = false;
  }
  if (closedir(stream) != 0) {
    success = false;
  }
  stream = NULL;
  if (!success) {
    goto done;
  }
  qsort(names.items, names.count, sizeof(*names.items),
        compare_string_pointers);
  for (index = 0U; success && index < names.count; index++) {
    char *path;
    char *text;
    size_t text_size;
    char *cursor;
    size_t line;

    path = joined_path(directory, names.items[index]);
    text = NULL;
    text_size = 0U;
    success = path != NULL && read_file(path, &text, &text_size);
    free(path);
    if (!success) {
      free(text);
      break;
    }
    cursor = text;
    line = 1U;
    while (success && *cursor != '\0') {
      char *end;
      char *comment;
      char *token;
      char *save;

      end = strchr(cursor, '\n');
      if (end != NULL) {
        *end = '\0';
      }
      comment = strchr(cursor, '#');
      if (comment != NULL) {
        *comment = '\0';
      }
      for (comment = cursor; *comment != '\0'; comment++) {
        if (*comment == '"') {
          *comment = ' ';
        }
      }
      save = NULL;
      token = strtok_r(cursor, " \t\r", &save);
      while (success && token != NULL) {
        if (token[0] == '-') {
          success =
              flag_occurrence_add(occurrences, token, names.items[index], line);
        }
        token = strtok_r(NULL, " \t\r", &save);
      }
      if (end == NULL) {
        break;
      }
      cursor = end + 1;
      line++;
    }
    free(text);
  }

done:
  if (stream != NULL) {
    closedir(stream);
  }
  string_list_destroy(&names);
  return success;
}

static bool flag_occurrence_add(struct flag_occurrence_list *list,
                                const char *token, const char *file,
                                size_t line) {
  struct flag_occurrence *items;
  size_t capacity;
  struct flag_occurrence *item;
  bool success;

  success = false;
  if (list->count == list->capacity) {
    capacity = list->capacity == 0U ? 128U : list->capacity * 2U;
    if (capacity < list->capacity ||
        capacity > SIZE_MAX / sizeof(*list->items)) {
      goto done;
    }
    items = realloc(list->items, capacity * sizeof(*list->items));
    if (items == NULL) {
      goto done;
    }
    list->items = items;
    list->capacity = capacity;
  }
  item = &list->items[list->count];
  memset(item, 0, sizeof(*item));
  item->token = duplicate_text(token);
  item->file = duplicate_text(file);
  item->line = line;
  item->order = list->count;
  success = item->token != NULL && item->file != NULL &&
            split_flag_token(token, &item->base, &item->value, &item->negated);
  if (!success) {
    free(item->token);
    free(item->file);
    free(item->base);
    free(item->value);
    memset(item, 0, sizeof(*item));
    goto done;
  }
  list->count++;

done:
  return success;
}

static void flag_occurrence_list_destroy(struct flag_occurrence_list *list) {
  size_t index;

  for (index = 0U; index < list->count; index++) {
    free(list->items[index].token);
    free(list->items[index].base);
    free(list->items[index].value);
    free(list->items[index].file);
  }
  free(list->items);
  memset(list, 0, sizeof(*list));
}

static bool split_flag_token(const char *token, char **base, char **value,
                             bool *negated) {
  const char *normalized;
  const char *separator;
  const char *body;
  char prefix[3];
  size_t base_size;
  bool success;

  normalized = token;
  if (strcmp(token, "-Wshadow-local") == 0) {
    normalized = "-Wshadow=local";
  } else if (strcmp(token, "-Wshadow-compatible-local") == 0) {
    normalized = "-Wshadow=compatible-local";
  }
  *negated = false;
  body = normalized;
  if (normalized[0] == '-' &&
      (normalized[1] == 'W' || normalized[1] == 'f' || normalized[1] == 'g') &&
      strncmp(normalized + 2, "no-", 3U) == 0) {
    prefix[0] = '-';
    prefix[1] = normalized[1];
    prefix[2] = '\0';
    body = normalized + 5;
    base_size = strlen(prefix) + strlen(body);
    *base = malloc(base_size + 1U);
    if (*base != NULL) {
      snprintf(*base, base_size + 1U, "%s%s", prefix, body);
    }
    *value = duplicate_text("");
    *negated = true;
    success = *base != NULL && *value != NULL;
    goto done;
  }
  separator = strchr(normalized, '=');
  if (separator == NULL) {
    *base = duplicate_text(normalized);
    *value = duplicate_text("");
  } else {
    *base = duplicate_range(normalized, (size_t)(separator - normalized));
    *value = duplicate_text(separator);
  }
  success = *base != NULL && *value != NULL;

done:
  return success;
}

static bool flag_base_is_additive(const char *base) {
  static const char *const additive[] = {"-fsanitize",
                                         "-fno-sanitize",
                                         "-fsanitize-recover",
                                         "-fno-sanitize-recover",
                                         "-fsanitize-trap",
                                         "-fno-sanitize-trap",
                                         "-Wsuggest-attribute",
                                         "-fanalyzer-checker",
                                         "-fplugin",
                                         "-include",
                                         "-imacros",
                                         "-D",
                                         "-U",
                                         "-I",
                                         "-L",
                                         "-l"};
  size_t index;
  bool result;

  result = false;
  for (index = 0U; index < sizeof(additive) / sizeof(additive[0]); index++) {
    if (strcmp(base, additive[index]) == 0) {
      result = true;
      break;
    }
  }
  return result;
}

static int flag_strength(const char *base, const char *value, bool *known) {
  static const char *const bases[] = {"-Wshadow",
                                      "-Wshift-overflow",
                                      "-Wcast-align",
                                      "-Wstrict-aliasing",
                                      "-Wattribute-alias",
                                      "-Wformat",
                                      "-Wimplicit-fallthrough",
                                      "-Warray-bounds",
                                      "-Wuse-after-free"};
  static const char *const values[][6] = {
      {"=compatible-local", "=local", "=global", NULL},
      {"=1", "=2", NULL},
      {"=base", "=strict", NULL},
      {"=0", "=1", "=2", "=3", NULL},
      {"=1", "=2", NULL},
      {"=1", "=2", NULL},
      {"=1", "=2", "=3", "=4", "=5", NULL},
      {"=1", "=2", NULL},
      {"=1", "=2", "=3", NULL}};
  static const char *const plain[] = {"=global", "=1", "=base", "=3", "=1",
                                      "=1",      "=3", "=1",    "=2"};
  size_t axis;
  size_t level;
  const char *normalized;
  char *end;
  long numeric;
  int result;

  *known = false;
  result = 0;
  for (axis = 0U; axis < sizeof(bases) / sizeof(bases[0]); axis++) {
    if (strcmp(base, bases[axis]) != 0) {
      continue;
    }
    normalized = value[0] == '\0' ? plain[axis] : value;
    for (level = 0U; values[axis][level] != NULL; level++) {
      if (strcmp(normalized, values[axis][level]) == 0) {
        result = (int)level;
        *known = true;
        break;
      }
    }
    goto done;
  }
  if (value[0] == '=' && value[1] != '\0') {
    errno = 0;
    numeric = strtol(value + 1, &end, 10);
    if (errno == 0 && *end == '\0' && numeric >= 0 && numeric <= INT32_MAX) {
      result = (int)numeric;
      *known = true;
    }
  }

done:
  return result;
}

static int compare_string_pointers(const void *left, const void *right) {
  const char *const *left_text;
  const char *const *right_text;
  int result;

  left_text = left;
  right_text = right;
  result = strcmp(*left_text, *right_text);
  return result;
}

static void performance_sample_destroy(struct performance_sample *sample) {
  size_t index;

  for (index = 0U; index < sample->record_count; index++) {
    free(sample->records[index].result);
    free(sample->records[index].input_identity);
    free(sample->records[index].id);
  }
  free(sample->records);
  memset(sample, 0, sizeof(*sample));
}

static void
performance_sample_list_destroy(struct performance_sample_list *samples) {
  size_t index;

  for (index = 0U; index < samples->count; index++) {
    performance_sample_destroy(&samples->items[index]);
  }
  free(samples->items);
  memset(samples, 0, sizeof(*samples));
}

static bool json_token_uint64_value(const struct json_document *document,
                                    size_t token, uint64_t *value) {
  char *text;
  char *end;
  unsigned long long parsed;
  bool success;

  text = NULL;
  success =
      token < document->count && document->tokens[token].kind == JSON_PRIMITIVE;
  if (success) {
    text = duplicate_range(document->text + document->tokens[token].start,
                           document->tokens[token].end -
                               document->tokens[token].start);
    success = text != NULL;
  }
  if (success) {
    errno = 0;
    parsed = strtoull(text, &end, 10);
    success = errno == 0 && *end == '\0' && parsed <= UINT64_MAX;
  }
  if (success) {
    *value = (uint64_t)parsed;
  }
  free(text);
  return success;
}

static char *json_token_raw(const struct json_document *document,
                            size_t token) {
  char *result;

  result = NULL;
  if (token < document->count) {
    result = duplicate_range(document->text + document->tokens[token].start,
                             document->tokens[token].end -
                                 document->tokens[token].start);
  }
  return result;
}

static bool performance_sample_load(const char *path,
                                    struct performance_sample *sample) {
  struct json_document document;
  size_t schema;
  size_t outcome;
  size_t mode;
  size_t cache;
  size_t reused;
  size_t elapsed;
  size_t records;
  size_t index;
  bool success;

  json_document_init(&document);
  success =
      json_document_load(path, &document) && document.count > 0U &&
      document.tokens[0].kind == JSON_OBJECT &&
      json_object_get(&document, 0U, "schema", &schema) &&
      json_token_equals(&document, schema, "p101-check-graph-receipt-v2") &&
      json_object_get(&document, 0U, "outcome", &outcome) &&
      json_token_equals(&document, outcome, "clean") &&
      json_object_get(&document, 0U, "mode", &mode) &&
      json_token_equals(&document, mode, "measurement") &&
      json_object_get(&document, 0U, "cache", &cache) &&
      document.tokens[cache].kind == JSON_OBJECT &&
      json_object_get(&document, cache, "reused", &reused) &&
      json_token_equals(&document, reused, "0") &&
      json_object_get(&document, 0U, "elapsed_ns", &elapsed) &&
      json_token_uint64_value(&document, elapsed, &sample->elapsed_ns) &&
      json_object_get(&document, 0U, "records", &records) &&
      document.tokens[records].kind == JSON_ARRAY;
  if (!success) {
    if (document.count > 0U &&
        json_object_get(&document, 0U, "cache", &cache) &&
        json_object_get(&document, cache, "reused", &reused) &&
        !json_token_equals(&document, reused, "0")) {
      printf("compare-check-performance: %s: performance sample reused cached "
             "nodes\n",
             path);
    } else {
      printf("compare-check-performance: %s: invalid performance receipt\n",
             path);
    }
    goto done;
  }
  sample->records =
      calloc(document.tokens[records].children, sizeof(*sample->records));
  success = sample->records != NULL;
  for (index = 0U; success && index < document.tokens[records].children;
       index++) {
    size_t record;
    size_t record_outcome;
    size_t id;
    size_t identity;
    size_t result;
    struct performance_record *destination;

    success = json_array_get(&document, records, index, &record) &&
              document.tokens[record].kind == JSON_OBJECT &&
              json_object_get(&document, record, "outcome", &record_outcome);
    if (!success || !json_token_equals(&document, record_outcome, "clean")) {
      continue;
    }
    destination = &sample->records[sample->record_count];
    success = json_object_get(&document, record, "id", &id) &&
              json_object_get(&document, record, "input_identity", &identity) &&
              json_object_get(&document, record, "result", &result);
    if (success) {
      destination->id = json_token_string(&document, id);
      destination->input_identity = json_token_string(&document, identity);
      destination->result = json_token_raw(&document, result);
      success = destination->id != NULL &&
                destination->input_identity != NULL &&
                destination->result != NULL;
    }
    if (success) {
      sample->record_count++;
    }
  }
  if (success && sample->record_count == 0U) {
    success = false;
  }

done:
  if (!success) {
    performance_sample_destroy(sample);
  }
  json_document_destroy(&document);
  return success;
}

static bool performance_sample_add(struct performance_sample_list *samples,
                                   const char *path) {
  struct performance_sample *items;
  size_t capacity;
  bool success;

  success = true;
  if (samples->count == samples->capacity) {
    capacity = samples->capacity == 0U ? 8U : samples->capacity * 2U;
    items = realloc(samples->items, capacity * sizeof(*samples->items));
    success = items != NULL;
    if (success) {
      samples->items = items;
      samples->capacity = capacity;
    }
  }
  if (success) {
    memset(&samples->items[samples->count], 0,
           sizeof(samples->items[samples->count]));
    success = performance_sample_load(path, &samples->items[samples->count]);
  }
  if (success) {
    samples->count++;
  }
  return success;
}

static const struct performance_record *
performance_record_find(const struct performance_sample *sample,
                        const char *identifier) {
  const struct performance_record *record;
  size_t index;

  record = NULL;
  for (index = 0U; index < sample->record_count; index++) {
    if (strcmp(sample->records[index].id, identifier) == 0) {
      record = &sample->records[index];
      break;
    }
  }
  return record;
}

static bool
performance_sample_matches(const struct performance_sample *reference,
                           const struct performance_sample *sample) {
  size_t index;
  bool matches;

  matches = reference->record_count == sample->record_count;
  for (index = 0U; matches && index < reference->record_count; index++) {
    const struct performance_record *expected;
    const struct performance_record *actual;

    expected = &reference->records[index];
    actual = performance_record_find(sample, expected->id);
    if (actual == NULL) {
      printf("compare-check-performance: sample node sets differ\n");
      matches = false;
    } else if (strcmp(actual->input_identity, expected->input_identity) != 0) {
      printf("compare-check-performance: %s: source/workload/tool identity "
             "differs\n",
             expected->id);
      matches = false;
    } else if (strcmp(actual->result, expected->result) != 0) {
      printf("compare-check-performance: %s: result identity differs\n",
             expected->id);
      matches = false;
    }
  }
  if (!matches && reference->record_count != sample->record_count) {
    printf("compare-check-performance: sample node sets differ\n");
  }
  return matches;
}

static bool
performance_samples_match(const struct performance_sample_list *baseline,
                          const struct performance_sample_list *candidate) {
  const struct performance_sample *reference;
  size_t index;
  bool matches;

  reference = &baseline->items[0];
  matches = true;
  for (index = 0U; matches && index < baseline->count; index++) {
    matches = performance_sample_matches(reference, &baseline->items[index]);
  }
  for (index = 0U; matches && index < candidate->count; index++) {
    matches = performance_sample_matches(reference, &candidate->items[index]);
  }
  return matches;
}

static int compare_uint64_values(const void *left, const void *right) {
  uint64_t left_value;
  uint64_t right_value;
  int result;

  left_value = *(const uint64_t *)left;
  right_value = *(const uint64_t *)right;
  result = left_value < right_value ? -1 : left_value > right_value ? 1 : 0;
  return result;
}

static uint64_t performance_median(const uint64_t *values, size_t count) {
  uint64_t result;

  if ((count % 2U) != 0U) {
    result = values[count / 2U];
  } else {
    uint64_t left;
    uint64_t right;

    left = values[(count / 2U) - 1U];
    right = values[count / 2U];
    result = left + ((right - left) / 2U);
  }
  return result;
}

static void lesson_entries_destroy(struct lesson_entries *entries) {
  string_list_destroy(&entries->urls);
  string_list_destroy(&entries->paths);
  string_list_destroy(&entries->lesson_ids);
  string_list_destroy(&entries->ids);
}

static bool lesson_finding_id_valid(const char *finding_id) {
  size_t length;
  size_t index;
  bool valid;

  length = strlen(finding_id);
  valid = length >= 10U && starts_with(finding_id, "P101-") &&
          finding_id[5] >= 'A' && finding_id[5] <= 'Z' &&
          finding_id[length - 4U] == '-';
  for (index = 5U; valid && index < length - 4U; index++) {
    char value;

    value = finding_id[index];
    valid = (value >= 'A' && value <= 'Z') || (value >= '0' && value <= '9') ||
            value == '_' || value == '-';
  }
  for (index = length - 3U; valid && index < length; index++) {
    valid = finding_id[index] >= '0' && finding_id[index] <= '9';
  }
  return valid;
}

static char *lesson_enum_name(const char *finding_id) {
  static const char prefix[] = "P101_TOOL_FINDING_";
  const char *suffix;
  size_t size;
  size_t index;
  char *result;

  suffix = finding_id + strlen("P101-");
  size = sizeof(prefix) + strlen(suffix);
  result = malloc(size);
  if (result != NULL) {
    snprintf(result, size, "%s%s", prefix, suffix);
    for (index = sizeof(prefix) - 1U; result[index] != '\0'; index++) {
      if (result[index] == '-') {
        result[index] = '_';
      }
    }
  }
  return result;
}

static bool lesson_relative_path_valid(const char *path) {
  const char *cursor;
  bool valid;

  valid = path[0] != '\0' && path[0] != '/';
  cursor = path;
  while (valid && *cursor != '\0') {
    const char *separator;
    size_t component_size;

    separator = strchr(cursor, '/');
    component_size =
        separator == NULL ? strlen(cursor) : (size_t)(separator - cursor);
    valid = component_size > 0U &&
            !(component_size == 2U && cursor[0] == '.' && cursor[1] == '.');
    cursor = separator == NULL ? cursor + component_size : separator + 1;
  }
  return valid;
}

static char *path_parent_copy(const char *path) {
  char *parent;
  char *separator;

  parent = duplicate_text(path);
  if (parent != NULL) {
    separator = strrchr(parent, '/');
    if (separator == NULL) {
      free(parent);
      parent = duplicate_text(".");
    } else if (separator == parent) {
      separator[1] = '\0';
    } else {
      *separator = '\0';
    }
  }
  return parent;
}

static bool lesson_entries_load(const char *catalog_path,
                                struct lesson_entries *entries) {
  struct json_document document;
  struct string_list seen_enums;
  size_t schema;
  size_t base_token;
  size_t lessons;
  char *url_base;
  char *lessons_directory;
  char *playground_directory;
  size_t lesson_index;
  bool success;

  json_document_init(&document);
  memset(&seen_enums, 0, sizeof(seen_enums));
  url_base = NULL;
  lessons_directory = NULL;
  playground_directory = NULL;
  success =
      json_document_load(catalog_path, &document) && document.count > 0U &&
      document.tokens[0].kind == JSON_OBJECT &&
      json_object_get(&document, 0U, "schema", &schema) &&
      json_token_equals(&document, schema, "p101-finding-lesson-catalog-v3") &&
      json_object_get(&document, 0U, "url_base", &base_token) &&
      json_object_get(&document, 0U, "lessons", &lessons) &&
      document.tokens[lessons].kind == JSON_ARRAY;
  if (success) {
    url_base = json_token_string(&document, base_token);
    lessons_directory = path_parent_copy(catalog_path);
    playground_directory =
        lessons_directory != NULL ? path_parent_copy(lessons_directory) : NULL;
    success = url_base != NULL && url_base[0] != '\0' &&
              url_base[strlen(url_base) - 1U] == '/' &&
              playground_directory != NULL;
  }
  for (lesson_index = 0U;
       success && lesson_index < document.tokens[lessons].children;
       lesson_index++) {
    size_t lesson;
    size_t lesson_id_token;
    size_t lesson_path_token;
    size_t finding_ids;
    char *lesson_id;
    char *lesson_path;
    char *relative_path;
    char *local_path;
    size_t finding_index;

    lesson_id = NULL;
    lesson_path = NULL;
    relative_path = NULL;
    local_path = NULL;
    success =
        json_array_get(&document, lessons, lesson_index, &lesson) &&
        document.tokens[lesson].kind == JSON_OBJECT &&
        json_object_get(&document, lesson, "lesson_id", &lesson_id_token) &&
        json_object_get(&document, lesson, "path", &lesson_path_token) &&
        json_object_get(&document, lesson, "finding_ids", &finding_ids) &&
        document.tokens[finding_ids].kind == JSON_ARRAY;
    if (success) {
      lesson_id = json_token_string(&document, lesson_id_token);
      lesson_path = json_token_string(&document, lesson_path_token);
      success = lesson_id != NULL && lesson_id[0] != '\0' &&
                lesson_path != NULL && lesson_relative_path_valid(lesson_path);
    }
    if (success) {
      relative_path = joined_path("lessons", lesson_path);
      local_path = joined_path(playground_directory, relative_path);
      success = relative_path != NULL && local_path != NULL &&
                regular_file(local_path);
    }
    for (finding_index = 0U;
         success && finding_index < document.tokens[finding_ids].children;
         finding_index++) {
      size_t finding_token;
      char *finding_id;
      char *url;
      char *enum_name;
      size_t url_size;

      finding_id = NULL;
      url = NULL;
      enum_name = NULL;
      success =
          json_array_get(&document, finding_ids, finding_index, &finding_token);
      if (success) {
        finding_id = json_token_string(&document, finding_token);
        enum_name = finding_id != NULL ? lesson_enum_name(finding_id) : NULL;
        success = finding_id != NULL && lesson_finding_id_valid(finding_id) &&
                  enum_name != NULL &&
                  !string_list_contains(&entries->ids, finding_id) &&
                  !string_list_contains(&seen_enums, enum_name) &&
                  string_list_add(&seen_enums, duplicate_text(enum_name));
      }
      if (success) {
        url_size =
            strlen(url_base) + strlen(relative_path) + strlen(finding_id) + 2U;
        url = malloc(url_size);
        success = url != NULL;
        if (success) {
          snprintf(url, url_size, "%s%s#%s", url_base, relative_path,
                   finding_id);
        }
      }
      if (success) {
        success =
            string_list_add(&entries->ids, finding_id) &&
            string_list_add(&entries->lesson_ids, duplicate_text(lesson_id)) &&
            string_list_add(&entries->paths, duplicate_text(relative_path)) &&
            string_list_add(&entries->urls, url);
        if (success) {
          finding_id = NULL;
          url = NULL;
        }
      }
      free(enum_name);
      free(url);
      free(finding_id);
    }
    free(local_path);
    free(relative_path);
    free(lesson_path);
    free(lesson_id);
  }
  if (success && entries->ids.count == 0U) {
    success = false;
  }
  if (!success) {
    fprintf(stderr, "generate-tool-lesson-catalog: malformed catalog: %s\n",
            catalog_path);
    lesson_entries_destroy(entries);
  }
  free(playground_directory);
  free(lessons_directory);
  free(url_base);
  string_list_destroy(&seen_enums);
  json_document_destroy(&document);
  return success;
}

static bool tool_lesson_header_write(FILE *stream,
                                     const struct lesson_entries *entries) {
  size_t index;
  bool success;

  success = fputs("#ifndef P101_TOOL_SUPPORT_LESSON_CATALOG_H\n"
                  "#define P101_TOOL_SUPPORT_LESSON_CATALOG_H\n\n"
                  "/* Generated from playgrounds/lessons/manifest.json; do not "
                  "edit. */\n\n"
                  "#ifdef __cplusplus\n"
                  "extern \"C\"\n"
                  "{\n"
                  "#endif\n\n"
                  "    // clang-format off\n"
                  "    typedef enum\n"
                  "    {\n",
                  stream) >= 0;
  for (index = 0U; success && index < entries->ids.count; index++) {
    char *enum_name;

    enum_name = lesson_enum_name(entries->ids.items[index]);
    success = enum_name != NULL &&
              fprintf(stream, "        %s = %zu,\n", enum_name, index) >= 0;
    free(enum_name);
  }
  if (success) {
    success = fprintf(stream,
                      "        P101_TOOL_FINDING_COUNT = %zu\n"
                      "    } p101_tool_finding;\n\n"
                      "    struct p101_tool_rule_definition\n"
                      "    {\n"
                      "        const char *id;\n"
                      "        const char *lesson_id;\n"
                      "        const char *lesson_path;\n"
                      "        const char *lesson_url;\n"
                      "    };\n\n"
                      "    // clang-format on\n\n"
                      "    const struct p101_tool_rule_definition "
                      "*p101_tool_rule_definition_lookup(p101_tool_finding "
                      "finding);\n"
                      "    const struct p101_tool_rule_definition "
                      "*p101_tool_rule_definition_lookup_id(const char "
                      "*diagnostic_id);\n\n"
                      "#ifdef __cplusplus\n"
                      "}\n"
                      "#endif\n\n"
                      "#endif\n",
                      entries->ids.count) >= 0;
  }
  return success;
}

static bool tool_lesson_source_write(FILE *stream,
                                     const struct lesson_entries *entries) {
  char *first_enum;
  size_t index;
  bool success;

  first_enum = lesson_enum_name(entries->ids.items[0]);
  success = first_enum != NULL &&
            fputs("#include <errno.h>\n"
                  "#include <p101_tool_support/lesson_catalog.h>\n"
                  "#include <stddef.h>\n"
                  "#include <string.h>\n\n"
                  "/* Generated from playgrounds/lessons/manifest.json; do "
                  "not edit. */\n\n"
                  "const struct p101_tool_rule_definition "
                  "*p101_tool_rule_definition_lookup(p101_tool_finding "
                  "finding)\n"
                  "{\n"
                  "    // clang-format off\n"
                  "    static const struct p101_tool_rule_definition rules[] "
                  "= {\n",
                  stream) >= 0;
  for (index = 0U; success && index < entries->ids.count; index++) {
    success = fputs("        {", stream) >= 0 &&
              json_write_string(stream, entries->ids.items[index]) &&
              fputs(", ", stream) >= 0 &&
              json_write_string(stream, entries->lesson_ids.items[index]) &&
              fputs(", ", stream) >= 0 &&
              json_write_string(stream, entries->paths.items[index]) &&
              fputs(", ", stream) >= 0 &&
              json_write_string(stream, entries->urls.items[index]) &&
              fprintf(stream, "}%s\n",
                      index + 1U == entries->ids.count ? "" : ",") >= 0;
  }
  if (success) {
    success =
        fprintf(
            stream,
            "    };\n\n"
            "    // clang-format on\n"
            "    const struct p101_tool_rule_definition *rule;\n\n"
            "    if(finding >= P101_TOOL_FINDING_COUNT)\n"
            "    {\n"
            "        errno = EINVAL;\n"
            "        rule  = NULL;\n"
            "    }\n"
            "    else\n"
            "    {\n"
            "        rule = &rules[finding];\n"
            "    }\n"
            "    return rule;\n"
            "}\n\n"
            "const struct p101_tool_rule_definition "
            "*p101_tool_rule_definition_lookup_id(const char "
            "*diagnostic_id)\n"
            "{\n"
            "    const struct p101_tool_rule_definition "
            "*p101_single_result_;\n\n"
            "    p101_single_result_ = NULL;\n"
            "    if(diagnostic_id == NULL)\n"
            "    {\n"
            "        errno = EINVAL;\n"
            "        goto p101_single_exit_;\n"
            "    }\n"
            "    for(p101_tool_finding finding = %s; finding < "
            "P101_TOOL_FINDING_COUNT; finding++)\n"
            "    {\n"
            "        const struct p101_tool_rule_definition "
            "*definition;\n"
            "        int                                     comparison;\n\n"
            "        definition = "
            "p101_tool_rule_definition_lookup(finding);\n"
            "        comparison = strcmp(definition->id, diagnostic_id);\n"
            "        if(comparison == 0)\n"
            "        {\n"
            "            p101_single_result_ = definition;\n"
            "            break;\n"
            "        }\n"
            "    }\n\n"
            "p101_single_exit_:\n"
            "    return p101_single_result_;\n"
            "}\n",
            first_enum) >= 0;
  }
  free(first_enum);
  return success;
}

static bool generated_file_update(const char *path, const char *content,
                                  size_t content_size, bool check,
                                  bool *in_sync) {
  char *existing;
  size_t existing_size;
  bool success;

  existing = NULL;
  existing_size = 0U;
  *in_sync = regular_file(path) && read_file(path, &existing, &existing_size) &&
             existing_size == content_size &&
             memcmp(existing, content, content_size) == 0;
  free(existing);
  success = true;
  if (!*in_sync && check) {
    fprintf(stderr, "generated lesson catalog drift: %s\n", path);
  } else if (!*in_sync) {
    success = path_parent_directories(path) &&
              write_file(path, content, content_size);
    *in_sync = success;
    if (success) {
      printf("wrote %s\n", path);
    }
  }
  return success;
}

static bool tool_lesson_catalog_render(const char *catalog_path,
                                       const char *header_path,
                                       const char *source_path, bool check,
                                       bool *in_sync) {
  struct lesson_entries entries;
  FILE *header_stream;
  FILE *source_stream;
  char *header;
  char *source;
  size_t header_size;
  size_t source_size;
  bool header_sync;
  bool source_sync;
  bool success;

  memset(&entries, 0, sizeof(entries));
  header_stream = NULL;
  source_stream = NULL;
  header = NULL;
  source = NULL;
  header_size = 0U;
  source_size = 0U;
  header_sync = false;
  source_sync = false;
  success = lesson_entries_load(catalog_path, &entries);
  if (success) {
    header_stream = open_memstream(&header, &header_size);
    source_stream = open_memstream(&source, &source_size);
    success = header_stream != NULL && source_stream != NULL &&
              tool_lesson_header_write(header_stream, &entries) &&
              tool_lesson_source_write(source_stream, &entries);
  }
  if (header_stream != NULL && fclose(header_stream) != 0) {
    success = false;
  }
  header_stream = NULL;
  if (source_stream != NULL && fclose(source_stream) != 0) {
    success = false;
  }
  source_stream = NULL;
  if (success) {
    success = generated_file_update(header_path, header, header_size, check,
                                    &header_sync) &&
              generated_file_update(source_path, source, source_size, check,
                                    &source_sync);
  }
  *in_sync = success && header_sync && source_sync;
  if (header_stream != NULL) {
    fclose(header_stream);
  }
  if (source_stream != NULL) {
    fclose(source_stream);
  }
  free(source);
  free(header);
  lesson_entries_destroy(&entries);
  return success;
}

static bool inspect_rule_kind_supported(const char *kind) {
  static const char *const kinds[] = {"forbid-finding", "require-finding",
                                      "forbid-call",    "require-call",
                                      "require-edge",   "require-resource"};
  size_t index;
  bool supported;

  supported = false;
  for (index = 0U; index < sizeof(kinds) / sizeof(kinds[0]); index++) {
    if (strcmp(kind, kinds[index]) == 0) {
      supported = true;
      break;
    }
  }
  return supported;
}

static char *inspect_rule_identifier(const char *name) {
  char *identifier;
  size_t index;

  identifier = duplicate_text(name);
  if (identifier != NULL) {
    for (index = 0U; identifier[index] != '\0'; index++) {
      unsigned char value;

      value = (unsigned char)identifier[index];
      if (!(isalnum(value) != 0 || value == '_')) {
        identifier[index] = '_';
      }
    }
  }
  return identifier;
}

static bool inspect_rule_pack_write(FILE *stream, const char *path,
                                    struct string_list *pack_names) {
  static const char *const fields[] = {"id", "kind", "pattern", "title"};
  struct json_document document;
  struct string_list seen_ids;
  size_t schema;
  size_t name_token;
  size_t rules;
  char *name;
  char *identifier;
  size_t rule_index;
  bool success;

  json_document_init(&document);
  memset(&seen_ids, 0, sizeof(seen_ids));
  name = NULL;
  identifier = NULL;
  success = json_document_load(path, &document) && document.count > 0U &&
            document.tokens[0].kind == JSON_OBJECT &&
            json_object_get(&document, 0U, "schema", &schema) &&
            json_token_equals(&document, schema, "p101-rule-pack-v1") &&
            json_object_get(&document, 0U, "name", &name_token) &&
            json_object_get(&document, 0U, "rules", &rules) &&
            document.tokens[rules].kind == JSON_ARRAY;
  if (success) {
    name = json_token_string(&document, name_token);
    identifier = name != NULL ? inspect_rule_identifier(name) : NULL;
    success = name != NULL && name[0] != '\0' && identifier != NULL &&
              string_list_add(pack_names, duplicate_text(name));
  }
  if (success) {
    success = fprintf(stream,
                      "static const struct rule_definition rule_pack_%s[] =\n"
                      "{\n",
                      identifier) >= 0;
  }
  for (rule_index = 0U; success && rule_index < document.tokens[rules].children;
       rule_index++) {
    size_t rule;
    char *values[4];
    size_t field_index;

    memset(values, 0, sizeof(values));
    success = json_array_get(&document, rules, rule_index, &rule) &&
              document.tokens[rule].kind == JSON_OBJECT &&
              document.tokens[rule].children == 8U;
    for (field_index = 0U; success && field_index < 4U; field_index++) {
      size_t token;

      success = json_object_get(&document, rule, fields[field_index], &token);
      if (success) {
        values[field_index] = json_token_string(&document, token);
        success = values[field_index] != NULL && values[field_index][0] != '\0';
      }
    }
    if (success) {
      success = inspect_rule_kind_supported(values[1]) &&
                !string_list_contains(&seen_ids, values[0]) &&
                string_list_add(&seen_ids, duplicate_text(values[0]));
    }
    if (success) {
      success = fputs("    {", stream) >= 0;
    }
    for (field_index = 0U; success && field_index < 4U; field_index++) {
      if (field_index > 0U) {
        success = fputs(", ", stream) >= 0;
      }
      if (success) {
        success = json_write_string(stream, values[field_index]);
      }
    }
    if (success) {
      success = fputs("},\n", stream) >= 0;
    }
    for (field_index = 0U; field_index < 4U; field_index++) {
      free(values[field_index]);
    }
  }
  if (success) {
    success = fputs("};\n\n", stream) >= 0;
  } else {
    fprintf(stderr, "p101-bootstrap: malformed inspect rule pack: %s\n", path);
  }
  free(identifier);
  free(name);
  string_list_destroy(&seen_ids);
  json_document_destroy(&document);
  return success;
}

static bool inspect_rule_catalog_render(const char *rule_directory,
                                        const char *output_path, bool check,
                                        bool *in_sync) {
  struct string_list names;
  struct string_list pack_names;
  DIR *directory;
  struct dirent *entry;
  FILE *stream;
  char *generated;
  size_t generated_size;
  size_t index;
  bool success;

  memset(&names, 0, sizeof(names));
  memset(&pack_names, 0, sizeof(pack_names));
  directory = opendir(rule_directory);
  stream = NULL;
  generated = NULL;
  generated_size = 0U;
  success = directory != NULL;
  if (!success) {
    fprintf(stderr, "p101-bootstrap: cannot open rule directory: %s\n",
            rule_directory);
    goto done;
  }
  errno = 0;
  entry = readdir(directory);
  while (success && entry != NULL) {
    size_t length;

    length = strlen(entry->d_name);
    if (length > 5U && strcmp(entry->d_name + length - 5U, ".json") == 0) {
      success = string_list_add(&names, duplicate_text(entry->d_name));
    }
    entry = readdir(directory);
  }
  if (success && errno != 0) {
    success = false;
  }
  if (closedir(directory) != 0) {
    success = false;
  }
  directory = NULL;
  if (!success || names.count == 0U) {
    goto done;
  }
  qsort(names.items, names.count, sizeof(*names.items),
        compare_string_pointers);
  stream = open_memstream(&generated, &generated_size);
  success = stream != NULL &&
            fputs("/* Generated by p101-bootstrap inspect-rule-catalog. */\n\n",
                  stream) >= 0;
  for (index = 0U; success && index < names.count; index++) {
    char *path;

    path = joined_path(rule_directory, names.items[index]);
    success =
        path != NULL && inspect_rule_pack_write(stream, path, &pack_names);
    free(path);
  }
  if (success) {
    success = fputs("static const struct rule_pack_definition rule_packs[] =\n"
                    "{\n",
                    stream) >= 0;
  }
  for (index = 0U; success && index < pack_names.count; index++) {
    char *identifier;

    identifier = inspect_rule_identifier(pack_names.items[index]);
    success = identifier != NULL && fputs("    {", stream) >= 0 &&
              json_write_string(stream, pack_names.items[index]) &&
              fprintf(stream,
                      ", rule_pack_%s, sizeof(rule_pack_%s) / "
                      "sizeof(rule_pack_%s[0])},\n",
                      identifier, identifier, identifier) >= 0;
    free(identifier);
  }
  if (success) {
    success = fputs("};\n", stream) >= 0;
  }
  if (stream != NULL && fclose(stream) != 0) {
    success = false;
  }
  stream = NULL;
  if (success) {
    char *existing;
    size_t existing_size;

    existing = NULL;
    existing_size = 0U;
    *in_sync = regular_file(output_path) &&
               read_file(output_path, &existing, &existing_size) &&
               existing_size == generated_size &&
               memcmp(existing, generated, generated_size) == 0;
    free(existing);
    if (!check && !*in_sync) {
      success = path_parent_directories(output_path) &&
                write_file(output_path, generated, generated_size);
      *in_sync = success;
    }
    if (check && !*in_sync) {
      fprintf(stderr, "generated inspect rule catalog drift: %s\n",
              output_path);
    }
  }

done:
  if (directory != NULL) {
    closedir(directory);
  }
  if (stream != NULL) {
    fclose(stream);
  }
  free(generated);
  string_list_destroy(&pack_names);
  string_list_destroy(&names);
  return success;
}

static bool stack_paths_load(const char *scripts_root,
                             struct string_list *paths) {
  char *inventory_path;
  char *text;
  size_t text_size;
  char *cursor;
  bool success;

  inventory_path = joined_path(scripts_root, "contracts/p101-stack-paths.txt");
  text = NULL;
  text_size = 0U;
  success =
      inventory_path != NULL && read_file(inventory_path, &text, &text_size);
  if (!success) {
    fprintf(stderr, "p101-bootstrap: cannot read stack path inventory\n");
    goto done;
  }
  cursor = text;
  while (success && *cursor != '\0') {
    char *end;
    size_t length;
    char *path;

    end = strchr(cursor, '\n');
    if (end != NULL) {
      *end = '\0';
    }
    length = strlen(cursor);
    if (length > 0U && cursor[length - 1U] == '\r') {
      cursor[length - 1U] = '\0';
      length--;
    }
    path = duplicate_text(cursor);
    success = length > 0U && cursor[0] != '/' && strchr(cursor, '\\') == NULL &&
              path != NULL;
    if (success && paths->count > 0U) {
      success = strcmp(paths->items[paths->count - 1U], path) < 0;
    }
    if (success) {
      success = string_list_add(paths, path);
    } else {
      free(path);
    }
    if (end == NULL) {
      break;
    }
    cursor = end + 1;
  }
  if (paths->count == 0U) {
    success = false;
  }

done:
  free(text);
  free(inventory_path);
  return success;
}

static char *stack_artifact_path(const char *scripts_root,
                                 const char *relative) {
  const char *lock_override;
  char *scripts_resolved;
  char *workspace_root;
  char *joined;
  char *resolved;
  char *separator;
  size_t size;

  resolved = NULL;
  lock_override = getenv("P101_STACK_REPOS_LOCK");
  if (strcmp(relative, "repos.lock") == 0 && lock_override != NULL &&
      lock_override[0] != '\0') {
    resolved = realpath(lock_override, NULL);
    goto done;
  }
  if (relative[0] == '/' || relative[0] == '\0' ||
      strchr(relative, '\\') != NULL) {
    goto done;
  }
  scripts_resolved = realpath(scripts_root, NULL);
  if (scripts_resolved == NULL) {
    goto done;
  }
  workspace_root = duplicate_text(scripts_resolved);
  if (workspace_root == NULL) {
    free(scripts_resolved);
    goto done;
  }
  separator = strrchr(workspace_root, '/');
  if (separator == NULL || separator == workspace_root) {
    free(workspace_root);
    free(scripts_resolved);
    goto done;
  }
  *separator = '\0';
  size = strlen(scripts_resolved) + strlen(relative) + 2U;
  joined = malloc(size);
  if (joined != NULL) {
    snprintf(joined, size, "%s/%s", scripts_resolved, relative);
    resolved = realpath(joined, NULL);
    free(joined);
  }
  if (resolved != NULL && !path_is_within(workspace_root, resolved)) {
    free(resolved);
    resolved = NULL;
  }
  free(workspace_root);
  free(scripts_resolved);

done:
  return resolved;
}

static bool stack_artifacts_collect(const char *scripts_root,
                                    const struct string_list *paths,
                                    struct stack_artifact_list *artifacts) {
  size_t index;
  bool success;

  artifacts->items = calloc(paths->count, sizeof(*artifacts->items));
  artifacts->count = paths->count;
  success = artifacts->items != NULL;
  for (index = 0U; success && index < paths->count; index++) {
    char *absolute;
    char *payload;
    size_t payload_size;
    struct stat status;

    absolute = stack_artifact_path(scripts_root, paths->items[index]);
    payload = NULL;
    payload_size = 0U;
    success = absolute != NULL && stat(absolute, &status) == 0 &&
              S_ISREG(status.st_mode) &&
              read_file(absolute, &payload, &payload_size);
    if (!success) {
      fprintf(stderr, "p101-bootstrap: unavailable stack artifact: %s\n",
              paths->items[index]);
    } else {
      artifacts->items[index].path = duplicate_text(paths->items[index]);
      artifacts->items[index].bytes = payload_size;
      sha256_bytes((const unsigned char *)payload, payload_size,
                   artifacts->items[index].sha256);
      success = artifacts->items[index].path != NULL;
    }
    free(payload);
    free(absolute);
  }
  if (!success) {
    stack_artifact_list_destroy(artifacts);
  }
  return success;
}

static void stack_artifact_list_destroy(struct stack_artifact_list *artifacts) {
  size_t index;

  for (index = 0U; index < artifacts->count; index++) {
    free(artifacts->items[index].path);
  }
  free(artifacts->items);
  memset(artifacts, 0, sizeof(*artifacts));
}

static bool stack_contract_write(const char *contract_path,
                                 const struct stack_artifact_list *artifacts) {
  FILE *stream;
  size_t index;
  bool success;

  success = path_parent_directories(contract_path);
  stream = success ? fopen(contract_path, "w") : NULL;
  success = stream != NULL;
  if (!success) {
    goto done;
  }
  success = fputs("{\n  \"artifacts\": [\n", stream) >= 0;
  for (index = 0U; success && index < artifacts->count; index++) {
    success = fputs("    {\n      \"bytes\": ", stream) >= 0 &&
              fprintf(stream, "%zu", artifacts->items[index].bytes) >= 0 &&
              fputs(",\n      \"path\": ", stream) >= 0 &&
              json_write_string(stream, artifacts->items[index].path) &&
              fputs(",\n      \"sha256\": ", stream) >= 0 &&
              json_write_string(stream, artifacts->items[index].sha256) &&
              fprintf(stream, "\n    }%s\n",
                      index + 1U == artifacts->count ? "" : ",") >= 0;
  }
  success = success && fputs("  ],\n  \"does_not_prove\": ", stream) >= 0 &&
            json_write_string(stream, STACK_DOES_NOT_PROVE) &&
            fputs(",\n  \"schema\": ", stream) >= 0 &&
            json_write_string(stream, STACK_SCHEMA) &&
            fputs("\n}\n", stream) >= 0;
  if (fclose(stream) != 0) {
    success = false;
  }
  stream = NULL;

done:
  if (stream != NULL) {
    fclose(stream);
  }
  return success;
}

static bool stack_contract_refresh(const char *scripts_root,
                                   const char *contract_path) {
  struct string_list paths;
  struct stack_artifact_list artifacts;
  bool success;

  memset(&paths, 0, sizeof(paths));
  memset(&artifacts, 0, sizeof(artifacts));
  success = stack_paths_load(scripts_root, &paths) &&
            stack_artifacts_collect(scripts_root, &paths, &artifacts) &&
            stack_contract_write(contract_path, &artifacts);
  stack_artifact_list_destroy(&artifacts);
  string_list_destroy(&paths);
  return success;
}

static bool stack_contract_read(const char *contract_path,
                                const struct string_list *paths,
                                struct stack_artifact_list *artifacts) {
  struct json_document document;
  size_t schema;
  size_t statement;
  size_t array;
  size_t index;
  bool success;

  json_document_init(&document);
  success = json_document_load(contract_path, &document) &&
            document.count > 0U && document.tokens[0].kind == JSON_OBJECT &&
            document.tokens[0].children == 6U &&
            json_object_get(&document, 0U, "schema", &schema) &&
            json_token_equals(&document, schema, STACK_SCHEMA) &&
            json_object_get(&document, 0U, "does_not_prove", &statement) &&
            json_token_equals(&document, statement, STACK_DOES_NOT_PROVE) &&
            json_object_get(&document, 0U, "artifacts", &array) &&
            document.tokens[array].kind == JSON_ARRAY &&
            document.tokens[array].children == paths->count;
  if (!success) {
    goto done;
  }
  artifacts->items = calloc(paths->count, sizeof(*artifacts->items));
  artifacts->count = paths->count;
  success = artifacts->items != NULL;
  for (index = 0U; success && index < paths->count; index++) {
    size_t object;
    size_t path_token;
    size_t bytes_token;
    size_t digest_token;
    char *path;
    char *digest;

    path = NULL;
    digest = NULL;
    success = json_array_get(&document, array, index, &object) &&
              document.tokens[object].kind == JSON_OBJECT &&
              document.tokens[object].children == 6U &&
              json_object_get(&document, object, "path", &path_token) &&
              json_object_get(&document, object, "bytes", &bytes_token) &&
              json_object_get(&document, object, "sha256", &digest_token);
    if (success) {
      path = json_token_string(&document, path_token);
      digest = json_token_string(&document, digest_token);
      success = path != NULL && digest != NULL && strlen(digest) == 64U &&
                strcmp(path, paths->items[index]) == 0 &&
                json_token_size_value(&document, bytes_token,
                                      &artifacts->items[index].bytes);
    }
    if (success) {
      artifacts->items[index].path = path;
      memcpy(artifacts->items[index].sha256, digest, 65U);
      path = NULL;
    }
    free(digest);
    free(path);
  }

done:
  if (!success) {
    stack_artifact_list_destroy(artifacts);
  }
  json_document_destroy(&document);
  return success;
}

static bool stack_receipt_write(FILE *stream, bool passed,
                                const char *contract_sha256,
                                const struct stack_artifact_list *actual,
                                const struct string_list *mismatches) {
  FILE *canonical_stream;
  char *canonical;
  size_t canonical_size;
  char digest[65];
  size_t artifact_bytes;
  size_t index;
  bool success;

  artifact_bytes = 0U;
  for (index = 0U; index < actual->count; index++) {
    artifact_bytes += actual->items[index].bytes;
  }
  canonical = NULL;
  canonical_size = 0U;
  canonical_stream = open_memstream(&canonical, &canonical_size);
  success = canonical_stream != NULL;
  if (success) {
    success = fprintf(canonical_stream,
                      "{\"artifact_bytes\":%zu,\"artifact_count\":%zu,"
                      "\"contract_sha256\":\"%s\",\"does_not_prove\":",
                      artifact_bytes, actual->count, contract_sha256) >= 0 &&
              json_write_string(canonical_stream, STACK_DOES_NOT_PROVE) &&
              fputs(",\"mismatches\":[", canonical_stream) >= 0;
  }
  for (index = 0U; success && index < mismatches->count; index++) {
    if (index > 0U) {
      success = fputc(',', canonical_stream) != EOF;
    }
    if (success) {
      success = json_write_string(canonical_stream, mismatches->items[index]);
    }
  }
  if (success) {
    success = fprintf(canonical_stream, "],\"passed\":%s,\"schema\":\"%s\"}",
                      passed ? "true" : "false", STACK_RECEIPT_SCHEMA) >= 0;
  }
  if (canonical_stream != NULL && fclose(canonical_stream) != 0) {
    success = false;
  }
  if (!success) {
    free(canonical);
    goto done;
  }
  sha256_bytes((const unsigned char *)canonical, canonical_size, digest);
  success = fprintf(stream,
                    "{\"artifact_bytes\":%zu,\"artifact_count\":%zu,"
                    "\"contract_sha256\":\"%s\",\"does_not_prove\":",
                    artifact_bytes, actual->count, contract_sha256) >= 0 &&
            json_write_string(stream, STACK_DOES_NOT_PROVE) &&
            fputs(",\"mismatches\":[", stream) >= 0;
  for (index = 0U; success && index < mismatches->count; index++) {
    if (index > 0U) {
      success = fputc(',', stream) != EOF;
    }
    if (success) {
      success = json_write_string(stream, mismatches->items[index]);
    }
  }
  if (success) {
    success =
        fprintf(stream,
                "],\"passed\":%s,\"receipt_digest\":\"sha256:%s\","
                "\"schema\":\"%s\"}\n",
                passed ? "true" : "false", digest, STACK_RECEIPT_SCHEMA) >= 0;
  }
  free(canonical);

done:
  return success;
}

static int stack_contract_verify(const char *scripts_root,
                                 const char *contract_path,
                                 const char *receipt_path) {
  struct string_list paths;
  struct string_list mismatches;
  struct stack_artifact_list expected;
  struct stack_artifact_list actual;
  char *contract_text;
  size_t contract_size;
  char contract_digest[65];
  size_t index;
  FILE *receipt;
  bool success;
  bool passed;
  int status;

  memset(&paths, 0, sizeof(paths));
  memset(&mismatches, 0, sizeof(mismatches));
  memset(&expected, 0, sizeof(expected));
  memset(&actual, 0, sizeof(actual));
  contract_text = NULL;
  contract_size = 0U;
  receipt = NULL;
  status = 2;
  success = stack_paths_load(scripts_root, &paths) &&
            stack_contract_read(contract_path, &paths, &expected) &&
            stack_artifacts_collect(scripts_root, &paths, &actual) &&
            read_file(contract_path, &contract_text, &contract_size);
  if (!success) {
    stack_refusal_write(stderr, "invalid stack contract or artifact",
                        "invalid-input");
    goto done;
  }
  sha256_bytes((const unsigned char *)contract_text, contract_size,
               contract_digest);
  for (index = 0U; index < expected.count; index++) {
    if (expected.items[index].bytes != actual.items[index].bytes ||
        strcmp(expected.items[index].sha256, actual.items[index].sha256) != 0) {
      success = string_list_add(&mismatches,
                                duplicate_text(expected.items[index].path));
      if (!success) {
        goto done;
      }
    }
  }
  passed = mismatches.count == 0U;
  success = stack_receipt_write(stdout, passed, contract_digest, &actual,
                                &mismatches);
  if (success && strcmp(receipt_path, "-") != 0) {
    success = path_parent_directories(receipt_path);
    receipt = success ? fopen(receipt_path, "w") : NULL;
    success =
        receipt != NULL && stack_receipt_write(receipt, passed, contract_digest,
                                               &actual, &mismatches);
    if (receipt != NULL && fclose(receipt) != 0) {
      success = false;
    }
    receipt = NULL;
  }
  if (success) {
    status = passed ? 0 : 1;
  }

done:
  if (receipt != NULL) {
    fclose(receipt);
  }
  free(contract_text);
  stack_artifact_list_destroy(&actual);
  stack_artifact_list_destroy(&expected);
  string_list_destroy(&mismatches);
  string_list_destroy(&paths);
  return status;
}

static void sha256_bytes(const unsigned char *data, size_t length,
                         char output[65]) {
  static const char hex[] = "0123456789abcdef";
  struct sha256_state state;
  unsigned char digest[32];
  size_t index;

  state.words[0] = 0x6a09e667U;
  state.words[1] = 0xbb67ae85U;
  state.words[2] = 0x3c6ef372U;
  state.words[3] = 0xa54ff53aU;
  state.words[4] = 0x510e527fU;
  state.words[5] = 0x9b05688cU;
  state.words[6] = 0x1f83d9abU;
  state.words[7] = 0x5be0cd19U;
  state.bit_count = 0U;
  state.block_size = 0U;
  sha256_update(&state, data, length);
  sha256_finish(&state, digest);
  for (index = 0U; index < sizeof(digest); index++) {
    output[index * 2U] = hex[digest[index] >> 4U];
    output[(index * 2U) + 1U] = hex[digest[index] & 0x0fU];
  }
  output[64] = '\0';
}

static uint32_t sha256_rotate(uint32_t value, unsigned int amount) {
  uint64_t widened;
  uint64_t rotated;
  uint32_t result;

  widened = value;
  rotated = (widened >> amount) | (widened << (32U - amount));
  result = (uint32_t)(rotated & UINT32_MAX);
  return result;
}

static void sha256_transform(struct sha256_state *state,
                             const unsigned char block[64]) {
  static const uint32_t constants[64] = {
      0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU,
      0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U,
      0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U,
      0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
      0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U,
      0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
      0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
      0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
      0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U,
      0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U, 0x1e376c08U,
      0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU,
      0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
      0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U};
  uint32_t schedule[64];
  uint32_t a;
  uint32_t b;
  uint32_t c;
  uint32_t d;
  uint32_t e;
  uint32_t f;
  uint32_t g;
  uint32_t h;
  size_t index;

  for (index = 0U; index < 16U; index++) {
    size_t offset;

    offset = index * 4U;
    schedule[index] = (uint32_t)(((uint64_t)block[offset] << 24U) |
                                 ((uint64_t)block[offset + 1U] << 16U) |
                                 ((uint64_t)block[offset + 2U] << 8U) |
                                 (uint64_t)block[offset + 3U]);
  }
  for (index = 16U; index < 64U; index++) {
    uint32_t first;
    uint32_t second;

    first = sha256_rotate(schedule[index - 15U], 7U) ^
            sha256_rotate(schedule[index - 15U], 18U) ^
            (schedule[index - 15U] >> 3U);
    second = sha256_rotate(schedule[index - 2U], 17U) ^
             sha256_rotate(schedule[index - 2U], 19U) ^
             (schedule[index - 2U] >> 10U);
    schedule[index] = (uint32_t)((uint64_t)schedule[index - 16U] + first +
                                 schedule[index - 7U] + second);
  }
  a = state->words[0];
  b = state->words[1];
  c = state->words[2];
  d = state->words[3];
  e = state->words[4];
  f = state->words[5];
  g = state->words[6];
  h = state->words[7];
  for (index = 0U; index < 64U; index++) {
    uint32_t choose;
    uint32_t majority;
    uint32_t sigma_zero;
    uint32_t sigma_one;
    uint32_t temporary_one;
    uint32_t temporary_two;

    choose = (e & f) ^ ((~e) & g);
    majority = (a & b) ^ (a & c) ^ (b & c);
    sigma_zero =
        sha256_rotate(a, 2U) ^ sha256_rotate(a, 13U) ^ sha256_rotate(a, 22U);
    sigma_one =
        sha256_rotate(e, 6U) ^ sha256_rotate(e, 11U) ^ sha256_rotate(e, 25U);
    temporary_one = (uint32_t)((uint64_t)h + sigma_one + choose +
                               constants[index] + schedule[index]);
    temporary_two = (uint32_t)((uint64_t)sigma_zero + majority);
    h = g;
    g = f;
    f = e;
    e = (uint32_t)((uint64_t)d + temporary_one);
    d = c;
    c = b;
    b = a;
    a = (uint32_t)((uint64_t)temporary_one + temporary_two);
  }
  state->words[0] = (uint32_t)((uint64_t)state->words[0] + a);
  state->words[1] = (uint32_t)((uint64_t)state->words[1] + b);
  state->words[2] = (uint32_t)((uint64_t)state->words[2] + c);
  state->words[3] = (uint32_t)((uint64_t)state->words[3] + d);
  state->words[4] = (uint32_t)((uint64_t)state->words[4] + e);
  state->words[5] = (uint32_t)((uint64_t)state->words[5] + f);
  state->words[6] = (uint32_t)((uint64_t)state->words[6] + g);
  state->words[7] = (uint32_t)((uint64_t)state->words[7] + h);
}

static void sha256_update(struct sha256_state *state, const unsigned char *data,
                          size_t length) {
  size_t index;

  for (index = 0U; index < length; index++) {
    state->block[state->block_size] = data[index];
    state->block_size++;
    if (state->block_size == sizeof(state->block)) {
      sha256_transform(state, state->block);
      state->bit_count += 512U;
      state->block_size = 0U;
    }
  }
}

static void sha256_finish(struct sha256_state *state,
                          unsigned char digest[32]) {
  uint64_t total_bits;
  size_t index;

  total_bits = state->bit_count + ((uint64_t)state->block_size * 8U);
  state->block[state->block_size] = 0x80U;
  state->block_size++;
  if (state->block_size > 56U) {
    while (state->block_size < sizeof(state->block)) {
      state->block[state->block_size] = 0U;
      state->block_size++;
    }
    sha256_transform(state, state->block);
    state->block_size = 0U;
  }
  while (state->block_size < 56U) {
    state->block[state->block_size] = 0U;
    state->block_size++;
  }
  for (index = 0U; index < 8U; index++) {
    state->block[63U - index] = (unsigned char)(total_bits >> (index * 8U));
  }
  sha256_transform(state, state->block);
  for (index = 0U; index < 8U; index++) {
    digest[index * 4U] = (unsigned char)(state->words[index] >> 24U);
    digest[(index * 4U) + 1U] = (unsigned char)(state->words[index] >> 16U);
    digest[(index * 4U) + 2U] = (unsigned char)(state->words[index] >> 8U);
    digest[(index * 4U) + 3U] = (unsigned char)state->words[index];
  }
}

static bool starts_with(const char *text, const char *prefix) {
  size_t prefix_size;
  bool result;

  prefix_size = strlen(prefix);
  result = strncmp(text, prefix, prefix_size) == 0;
  return result;
}

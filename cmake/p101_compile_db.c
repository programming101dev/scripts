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

static const size_t JSON_NO_PARENT = SIZE_MAX;

static void usage(const char *program);
static int sanitize_main(int argc, char **argv);
static int analyze_main(int argc, char **argv);
static int ctu_main(int argc, char **argv);
static int format_workspace_main(int argc, char **argv);

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
          "  %s format-workspace FORMATTER RECEIPT check|apply SCRIPTS_ROOT\n",
          program, program, program, program);
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

static bool starts_with(const char *text, const char *prefix) {
  size_t prefix_size;
  bool result;

  prefix_size = strlen(prefix);
  result = strncmp(text, prefix, prefix_size) == 0;
  return result;
}

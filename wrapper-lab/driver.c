#include <p101_c/p101_stdio.h>
#include <p101_c/p101_stdlib.h>
#include <p101_env/env.h>
#include <p101_error/error.h>
#include <p101_io/p101_aio.h>
#include <p101_io/p101_fcntl.h>
#include <p101_io/p101_poll.h>
#include <p101_io/p101_stdio.h>
#include <p101_io/p101_unistd.h>
#include <p101_io/sys/p101_select.h>
#include <p101_io/sys/p101_uio.h>
#include <p101_ipc/p101_unistd.h>
#include <p101_ipc/sys/p101_ipc.h>
#include <p101_ipc/sys/p101_mman.h>
#include <p101_ipc/sys/p101_msg.h>
#include <p101_ipc/sys/p101_sem.h>
#include <p101_ipc/sys/p101_shm.h>
#include <p101_ipc/sys/p101_stat.h>
#include <p101_memory/p101_stdlib.h>
#include <p101_memory/sys/p101_mman.h>
#include <p101_process/p101_sched.h>
#include <p101_process/p101_setjmp.h>
#include <p101_process/p101_signal.h>
#include <p101_process/p101_spawn.h>
#include <p101_process/p101_stdio.h>
#include <p101_process/p101_stdlib.h>
#include <p101_process/p101_unistd.h>
#include <p101_process/sys/p101_resource.h>
#include <p101_process/sys/p101_times.h>
#include <p101_process/sys/p101_wait.h>
#include <p101_sync/p101_pthread.h>
#include <p101_sync/p101_semaphore.h>
#include <p101_thread/p101_pthread.h>
#include <p101_thread/p101_signal.h>

#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

enum { REPLAY_LENGTH = 1024, TOKEN_LENGTH = 32, BUFFER_LENGTH = 32 };

struct model {
  void *allocation;
  size_t allocation_size;
  int descriptors[2];
  size_t descriptor_count;
  FILE *stream;
  void *mapping;
  size_t mapping_size;
  pthread_mutex_t mutex;
  bool mutex_initialized;
  bool mutex_locked;
  pid_t child;
  bool child_live;
  pthread_t thread;
  bool thread_live;
  unsigned int thread_result;
  int pipe_fds[2];
  bool pipe_ready;
  bool pipe_written;
  size_t pipe_bytes;
  FILE *positioned_stream;
  int positioned_fd;
  bool positioned_written;
  size_t positioned_bytes;
};

static void reset_error(struct p101_error *err);
static bool run_operation(const struct p101_env *env, struct p101_error *err,
                          const char *scenario, const char *operation,
                          struct model *model);
static bool operation_is_known(const char *scenario, const char *operation);
static bool run_allocation(const struct p101_env *env, struct p101_error *err,
                           const char *operation, struct model *model);
static bool run_descriptor(const struct p101_env *env, struct p101_error *err,
                           const char *operation, struct model *model);
static bool run_stream(const struct p101_env *env, struct p101_error *err,
                       const char *operation, struct model *model);
static bool run_mapping(const struct p101_env *env, struct p101_error *err,
                        const char *operation, struct model *model);
static bool run_mutex(const struct p101_env *env, struct p101_error *err,
                      const char *operation, struct model *model);
static bool run_process(const struct p101_env *env, struct p101_error *err,
                        const char *operation, struct model *model);
static bool run_thread(const struct p101_env *env, struct p101_error *err,
                       const char *operation, struct model *model);
static bool run_short_io(const struct p101_env *env, struct p101_error *err,
                         const char *operation, struct model *model);
static bool run_positioned_short_io(const struct p101_env *env,
                                    struct p101_error *err,
                                    const char *operation, struct model *model);
static bool io_count_is_expected(const char *call_name, ssize_t actual,
                                 size_t full_count);
static void *thread_worker(void *argument);
static void cleanup_model(const struct p101_env *env, struct p101_error *err,
                          struct model *model);
static bool model_is_clean(const struct model *model);

int main(int argc, char *argv[]) {
  struct p101_error *err;
  struct p101_env *env;
  struct model model = {
      .descriptors = {-1, -1}, .pipe_fds = {-1, -1}, .positioned_fd = -1};
  char replay[REPLAY_LENGTH];
  char *save;
  char *operation;
  const char *scenario;
  size_t operations;
  bool valid;

  if (argc != 3 || strlen(argv[2]) >= sizeof(replay)) {
    fprintf(stderr, "usage: %s <scenario> <comma-separated-replay>\n", argv[0]);
    return 2;
  }
  scenario = argv[1];
  memcpy(replay, argv[2], strlen(argv[2]) + 1U);
  err = p101_error_create(false);
  if (err == NULL) {
    return 2;
  }
  env = p101_env_create(err, NULL);
  if (env == NULL) {
    p101_error_destroy(err);
    return 2;
  }

  operations = 0U;
  valid = true;
  save = NULL;
  operation = strtok_r(replay, ",", &save);
  while (operation != NULL) {
    if (strlen(operation) >= TOKEN_LENGTH ||
        !run_operation(env, err, scenario, operation, &model)) {
      valid = false;
      break;
    }
    operations++;
    operation = strtok_r(NULL, ",", &save);
  }

  /* Cleanup must not itself remain subject to the selected disruption. */
  p101_env_set_fault_injector(env, NULL, NULL);
  reset_error(err);
  cleanup_model(env, err, &model);
  if (p101_error_has_error(err) || !model_is_clean(&model)) {
    valid = false;
  }
  p101_env_complete_event_streams(env);
  p101_env_destroy(env);
  p101_error_destroy(err);
  printf("{\"schema\":\"p101-wrapper-lifecycle-case-v1\",\"scenario\":\"%s\","
         "\"operations\":%zu,\"clean\":%s}\n",
         scenario, operations, valid ? "true" : "false");
  return valid ? 0 : 1;
}

static void reset_error(struct p101_error *err) {
  if (p101_error_has_error(err)) {
    p101_error_reset(err);
  }
}

static bool run_operation(const struct p101_env *env, struct p101_error *err,
                          const char *scenario, const char *operation,
                          struct model *model) {
  bool valid;

  if (strcmp(scenario, "allocation") == 0) {
    valid = run_allocation(env, err, operation, model);
  } else if (strcmp(scenario, "descriptor") == 0) {
    valid = run_descriptor(env, err, operation, model);
  } else if (strcmp(scenario, "stream") == 0) {
    valid = run_stream(env, err, operation, model);
  } else if (strcmp(scenario, "mapping") == 0) {
    valid = run_mapping(env, err, operation, model);
  } else if (strcmp(scenario, "mutex") == 0) {
    valid = run_mutex(env, err, operation, model);
  } else if (strcmp(scenario, "process") == 0) {
    valid = run_process(env, err, operation, model);
  } else if (strcmp(scenario, "thread") == 0) {
    valid = run_thread(env, err, operation, model);
  } else if (strcmp(scenario, "short-read") == 0 ||
             strcmp(scenario, "short-write") == 0) {
    valid = run_short_io(env, err, operation, model);
  } else if (strcmp(scenario, "positioned-short-read") == 0 ||
             strcmp(scenario, "positioned-short-write") == 0) {
    valid = run_positioned_short_io(env, err, operation, model);
  } else {
    return false;
  }
  reset_error(err);
  return valid || operation_is_known(scenario, operation);
}

static bool operation_is_known(const char *scenario, const char *operation) {
  if (strcmp(scenario, "allocation") == 0) {
    return strcmp(operation, "acquire") == 0 ||
           strcmp(operation, "replace") == 0 ||
           strcmp(operation, "release") == 0;
  }
  if (strcmp(scenario, "descriptor") == 0) {
    return strcmp(operation, "open") == 0 ||
           strcmp(operation, "duplicate") == 0 ||
           strcmp(operation, "close") == 0;
  }
  if (strcmp(scenario, "stream") == 0) {
    return strcmp(operation, "open") == 0 || strcmp(operation, "close") == 0;
  }
  if (strcmp(scenario, "mapping") == 0) {
    return strcmp(operation, "map") == 0 || strcmp(operation, "unmap") == 0;
  }
  if (strcmp(scenario, "mutex") == 0) {
    return strcmp(operation, "initialize") == 0 ||
           strcmp(operation, "lock") == 0 || strcmp(operation, "unlock") == 0 ||
           strcmp(operation, "destroy") == 0;
  }
  if (strcmp(scenario, "process") == 0) {
    return strcmp(operation, "fork") == 0 || strcmp(operation, "wait") == 0;
  }
  if (strcmp(scenario, "thread") == 0) {
    return strcmp(operation, "create") == 0 || strcmp(operation, "join") == 0;
  }
  if (strcmp(scenario, "short-read") == 0 ||
      strcmp(scenario, "short-write") == 0) {
    return strcmp(operation, "pipe") == 0 || strcmp(operation, "write") == 0 ||
           strcmp(operation, "read") == 0 || strcmp(operation, "close") == 0;
  }
  if (strcmp(scenario, "positioned-short-read") == 0 ||
      strcmp(scenario, "positioned-short-write") == 0) {
    return strcmp(operation, "open") == 0 || strcmp(operation, "write") == 0 ||
           strcmp(operation, "read") == 0 || strcmp(operation, "close") == 0;
  }
  return false;
}

static bool run_allocation(const struct p101_env *env, struct p101_error *err,
                           const char *operation, struct model *model) {
  if (strcmp(operation, "acquire") == 0 && model->allocation == NULL) {
    model->allocation = p101_malloc(env, err, 16U);
    if (model->allocation != NULL) {
      model->allocation_size = 16U;
      memset(model->allocation, 0x5a, model->allocation_size);
    }
    return true;
  }
  if (strcmp(operation, "replace") == 0 && model->allocation != NULL) {
    void *replacement;

    replacement = p101_realloc(env, err, model->allocation, 32U);
    if (replacement != NULL) {
      model->allocation = replacement;
      model->allocation_size = 32U;
    }
    return true;
  }
  if (strcmp(operation, "release") == 0 && model->allocation != NULL) {
    p101_free(env, model->allocation);
    model->allocation = NULL;
    model->allocation_size = 0U;
    return true;
  }
  return false;
}

static bool run_descriptor(const struct p101_env *env, struct p101_error *err,
                           const char *operation, struct model *model) {
  if (strcmp(operation, "open") == 0 && model->descriptor_count == 0U) {
    int fd = p101_open(env, err, "/dev/null", O_RDWR);
    if (fd >= 0) {
      model->descriptors[0] = fd;
      model->descriptor_count = 1U;
    }
    return true;
  }
  if (strcmp(operation, "duplicate") == 0 && model->descriptor_count == 1U) {
    int fd = p101_dup(env, err, model->descriptors[0]);
    if (fd >= 0) {
      model->descriptors[1] = fd;
      model->descriptor_count = 2U;
    }
    return true;
  }
  if (strcmp(operation, "close") == 0 && model->descriptor_count > 0U) {
    size_t index = model->descriptor_count - 1U;
    if (p101_close(env, err, model->descriptors[index]) == 0) {
      model->descriptors[index] = -1;
      model->descriptor_count--;
    }
    return true;
  }
  return false;
}

static bool run_stream(const struct p101_env *env, struct p101_error *err,
                       const char *operation, struct model *model) {
  if (strcmp(operation, "open") == 0 && model->stream == NULL) {
    model->stream = p101_fopen(env, err, "/dev/null", "r");
    return true;
  }
  if (strcmp(operation, "close") == 0 && model->stream != NULL) {
    if (p101_fclose(env, err, model->stream) == 0) {
      model->stream = NULL;
    }
    return true;
  }
  return false;
}

static bool run_mapping(const struct p101_env *env, struct p101_error *err,
                        const char *operation, struct model *model) {
  if (strcmp(operation, "map") == 0 && model->mapping == NULL) {
    model->mapping = p101_mmap(env, err, NULL, 4096U, PROT_READ | PROT_WRITE,
                               MAP_PRIVATE | MAP_ANON, -1, 0);
    if (model->mapping == MAP_FAILED) {
      model->mapping = NULL;
    } else if (model->mapping != NULL) {
      model->mapping_size = 4096U;
      memset(model->mapping, 0xa5, model->mapping_size);
    }
    return true;
  }
  if (strcmp(operation, "unmap") == 0 && model->mapping != NULL) {
    if (p101_munmap(env, err, model->mapping, model->mapping_size) == 0) {
      model->mapping = NULL;
      model->mapping_size = 0U;
    }
    return true;
  }
  return false;
}

static bool run_mutex(const struct p101_env *env, struct p101_error *err,
                      const char *operation, struct model *model) {
  if (strcmp(operation, "initialize") == 0 && !model->mutex_initialized) {
    if (p101_pthread_mutex_init(env, err, &model->mutex, NULL) == 0) {
      model->mutex_initialized = true;
    }
    return true;
  }
  if (strcmp(operation, "lock") == 0 && model->mutex_initialized &&
      !model->mutex_locked) {
    if (p101_pthread_mutex_lock(env, err, &model->mutex) == 0) {
      model->mutex_locked = true;
    }
    return true;
  }
  if (strcmp(operation, "unlock") == 0 && model->mutex_locked) {
    if (p101_pthread_mutex_unlock(env, err, &model->mutex) == 0) {
      model->mutex_locked = false;
    }
    return true;
  }
  if (strcmp(operation, "destroy") == 0 && model->mutex_initialized &&
      !model->mutex_locked) {
    if (p101_pthread_mutex_destroy(env, err, &model->mutex) == 0) {
      model->mutex_initialized = false;
    }
    return true;
  }
  return false;
}

static bool run_process(const struct p101_env *env, struct p101_error *err,
                        const char *operation, struct model *model) {
  if (strcmp(operation, "fork") == 0 && !model->child_live) {
    pid_t child = p101_fork(env, err);
    if (child == 0) {
      p101_env_complete_event_streams(env);
      _exit(0);
    }
    if (child > 0) {
      model->child = child;
      model->child_live = true;
    }
    return true;
  }
  if (strcmp(operation, "wait") == 0 && model->child_live) {
    int status;
    if (p101_waitpid(env, err, model->child, &status, 0) == model->child) {
      if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        return false;
      }
      model->child_live = false;
    }
    return true;
  }
  return false;
}

static void *thread_worker(void *argument) {
  unsigned int *result = argument;
  (*result)++;
  return argument;
}

static bool run_thread(const struct p101_env *env, struct p101_error *err,
                       const char *operation, struct model *model) {
  if (strcmp(operation, "create") == 0 && !model->thread_live) {
    if (p101_pthread_create(env, err, &model->thread, NULL, thread_worker,
                            &model->thread_result) == 0) {
      model->thread_live = true;
    }
    return true;
  }
  if (strcmp(operation, "join") == 0 && model->thread_live) {
    void *result = NULL;
    if (p101_pthread_join(env, err, model->thread, &result) == 0) {
      if (result != &model->thread_result || model->thread_result == 0U) {
        return false;
      }
      model->thread_live = false;
    }
    return true;
  }
  return false;
}

static bool run_short_io(const struct p101_env *env, struct p101_error *err,
                         const char *operation, struct model *model) {
  static const char message[] = "abcdef";
  char buffer[BUFFER_LENGTH];

  if (strcmp(operation, "pipe") == 0 && !model->pipe_ready) {
    if (p101_pipe(env, err, model->pipe_fds) == 0) {
      model->pipe_ready = true;
    }
    return true;
  }
  if (strcmp(operation, "write") == 0 && model->pipe_ready &&
      !model->pipe_written) {
    ssize_t written =
        p101_write(env, err, model->pipe_fds[1], message, sizeof(message));
    if (io_count_is_expected("p101_write", written, sizeof(message))) {
      model->pipe_written = true;
      model->pipe_bytes = (size_t)written;
    } else if (!p101_error_has_error(err)) {
      return false;
    }
    return true;
  }
  if (strcmp(operation, "read") == 0 && model->pipe_ready &&
      model->pipe_written) {
    ssize_t read_count =
        p101_read(env, err, model->pipe_fds[0], buffer, sizeof(buffer));
    if (io_count_is_expected("p101_read", read_count, model->pipe_bytes)) {
      model->pipe_written = false;
      model->pipe_bytes = 0U;
    } else if (!p101_error_has_error(err)) {
      return false;
    }
    return true;
  }
  if (strcmp(operation, "close") == 0 && model->pipe_ready &&
      !model->pipe_written) {
    bool clean = true;
    if (p101_close(env, err, model->pipe_fds[0]) == 0) {
      model->pipe_fds[0] = -1;
    } else {
      clean = false;
    }
    reset_error(err);
    if (p101_close(env, err, model->pipe_fds[1]) == 0) {
      model->pipe_fds[1] = -1;
    } else {
      clean = false;
    }
    if (clean) {
      model->pipe_ready = false;
    }
    return true;
  }
  return false;
}

static bool run_positioned_short_io(const struct p101_env *env,
                                    struct p101_error *err,
                                    const char *operation,
                                    struct model *model) {
  static const char message[] = "abcdef";
  char buffer[BUFFER_LENGTH];

  if (strcmp(operation, "open") == 0 && model->positioned_stream == NULL) {
    model->positioned_stream = p101_tmpfile(env, err);
    if (model->positioned_stream != NULL) {
      model->positioned_fd = p101_fileno(env, err, model->positioned_stream);
      if (model->positioned_fd < 0) {
        return false;
      }
    }
    return true;
  }
  if (strcmp(operation, "write") == 0 && model->positioned_stream != NULL &&
      !model->positioned_written) {
    ssize_t written = p101_pwrite(env, err, model->positioned_fd, message,
                                  sizeof(message), 0);
    if (io_count_is_expected("p101_pwrite", written, sizeof(message))) {
      model->positioned_written = true;
      model->positioned_bytes = (size_t)written;
    } else if (!p101_error_has_error(err)) {
      return false;
    }
    return true;
  }
  if (strcmp(operation, "read") == 0 && model->positioned_stream != NULL &&
      model->positioned_written) {
    ssize_t read_count =
        p101_pread(env, err, model->positioned_fd, buffer, sizeof(buffer), 0);
    if (io_count_is_expected("p101_pread", read_count,
                             model->positioned_bytes)) {
      model->positioned_written = false;
      model->positioned_bytes = 0U;
    } else if (!p101_error_has_error(err)) {
      return false;
    }
    return true;
  }
  if (strcmp(operation, "close") == 0 && model->positioned_stream != NULL &&
      !model->positioned_written) {
    if (p101_fclose(env, err, model->positioned_stream) == 0) {
      model->positioned_stream = NULL;
      model->positioned_fd = -1;
      model->positioned_bytes = 0U;
    }
    return true;
  }
  return false;
}

static bool io_count_is_expected(const char *call_name, ssize_t actual,
                                 size_t full_count) {
  const char *mode = getenv("P101_FAULT_MODE");
  const char *target = getenv("P101_FAULT_NAME");
  const char *amount_text = getenv("P101_FAULT_AMOUNT");

  if (mode != NULL && target != NULL && amount_text != NULL &&
      strcmp(mode, "short") == 0 && strcmp(target, call_name) == 0) {
    char *end = NULL;
    unsigned long amount = strtoul(amount_text, &end, 10);
    return end != amount_text && *end == '\0' && actual >= 0 &&
           (size_t)actual == amount;
  }
  return actual >= 0 && (size_t)actual == full_count;
}

static void cleanup_model(const struct p101_env *env, struct p101_error *err,
                          struct model *model) {
  if (model->allocation != NULL) {
    p101_free(env, model->allocation);
    model->allocation = NULL;
  }
  while (model->descriptor_count > 0U) {
    size_t index = model->descriptor_count - 1U;
    if (p101_close(env, err, model->descriptors[index]) != 0) {
      return;
    }
    model->descriptors[index] = -1;
    model->descriptor_count--;
  }
  if (model->stream != NULL && p101_fclose(env, err, model->stream) == 0) {
    model->stream = NULL;
  }
  if (model->mapping != NULL &&
      p101_munmap(env, err, model->mapping, model->mapping_size) == 0) {
    model->mapping = NULL;
  }
  if (model->mutex_locked &&
      p101_pthread_mutex_unlock(env, err, &model->mutex) == 0) {
    model->mutex_locked = false;
  }
  if (model->mutex_initialized &&
      p101_pthread_mutex_destroy(env, err, &model->mutex) == 0) {
    model->mutex_initialized = false;
  }
  if (model->child_live) {
    int status;
    if (p101_waitpid(env, err, model->child, &status, 0) == model->child) {
      model->child_live = false;
    }
  }
  reset_error(err);
  if (model->thread_live &&
      p101_pthread_join(env, err, model->thread, NULL) == 0) {
    model->thread_live = false;
  }
  if (model->pipe_fds[0] >= 0) {
    p101_close(env, err, model->pipe_fds[0]);
    model->pipe_fds[0] = -1;
  }
  reset_error(err);
  if (model->pipe_fds[1] >= 0) {
    p101_close(env, err, model->pipe_fds[1]);
    model->pipe_fds[1] = -1;
  }
  model->pipe_ready = false;
  model->pipe_written = false;
  model->pipe_bytes = 0U;
  reset_error(err);
  if (model->positioned_stream != NULL &&
      p101_fclose(env, err, model->positioned_stream) == 0) {
    model->positioned_stream = NULL;
    model->positioned_fd = -1;
  }
  model->positioned_written = false;
  model->positioned_bytes = 0U;
}

static bool model_is_clean(const struct model *model) {
  return model->allocation == NULL && model->descriptor_count == 0U &&
         model->stream == NULL && model->mapping == NULL &&
         !model->mutex_initialized && !model->mutex_locked &&
         !model->child_live && !model->thread_live && !model->pipe_ready &&
         model->pipe_fds[0] < 0 && model->pipe_fds[1] < 0 &&
         model->positioned_stream == NULL && model->positioned_fd < 0;
}

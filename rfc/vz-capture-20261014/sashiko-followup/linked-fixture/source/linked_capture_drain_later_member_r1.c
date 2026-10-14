#define _POSIX_C_SOURCE 200809L

#include <alsa/asoundlib.h>
#include <alloca.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Observed linked-group pointer order is substream 1 then substream 0. */
#define XRUN_FAULT_SUBSTREAM 0
#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)

static int sleep_ms(unsigned int milliseconds)
{
	struct timespec request = {
		.tv_sec = milliseconds / 1000,
		.tv_nsec = (long)(milliseconds % 1000) * 1000000L,
	};

	while (nanosleep(&request, &request) < 0) {
		if (errno != EINTR)
			return -errno;
	}
	return 0;
}

static int configure_capture(snd_pcm_t *pcm)
{
	snd_pcm_hw_params_t *hw;
	snd_pcm_sw_params_t *sw;
	snd_pcm_uframes_t period = 1024;
	snd_pcm_uframes_t buffer = period * 4;
	snd_pcm_uframes_t actual_period;
	snd_pcm_uframes_t actual_buffer;
	snd_pcm_uframes_t avail_min = 1;
	snd_pcm_uframes_t start_threshold = 1;
	unsigned int rate = 48000;
	int direction = 0;
	int rc;

	snd_pcm_hw_params_alloca(&hw);
	snd_pcm_sw_params_alloca(&sw);
	if ((rc = snd_pcm_hw_params_any(pcm, hw)) < 0 ||
	    (rc = snd_pcm_hw_params_set_access(pcm, hw,
					       SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
	    (rc = snd_pcm_hw_params_set_format(pcm, hw,
					       SND_PCM_FORMAT_S16_LE)) < 0 ||
	    (rc = snd_pcm_hw_params_set_channels(pcm, hw, 1)) < 0 ||
	    (rc = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate,
						 &direction)) < 0 ||
	    (rc = snd_pcm_hw_params_set_period_size_near(pcm, hw, &period,
							 &direction)) < 0)
		return rc;
	buffer = period * 4;
	if ((rc = snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer)) < 0 ||
	    (rc = snd_pcm_hw_params(pcm, hw)) < 0 ||
	    (rc = snd_pcm_hw_params_get_period_size(hw, &actual_period,
						    &direction)) < 0 ||
	    (rc = snd_pcm_hw_params_get_buffer_size(hw, &actual_buffer)) < 0 ||
	    (rc = snd_pcm_sw_params_current(pcm, sw)) < 0 ||
	    (rc = snd_pcm_sw_params_set_avail_min(pcm, sw, avail_min)) < 0 ||
	    (rc = snd_pcm_sw_params_set_start_threshold(pcm, sw,
							 start_threshold)) < 0 ||
	    (rc = snd_pcm_sw_params_set_stop_threshold(pcm, sw,
							 actual_buffer)) < 0 ||
	    (rc = snd_pcm_sw_params(pcm, sw)) < 0)
		return rc;
	printf("CONFIG rate=%u format=S16_LE channels=1 period_frames=%" PRIu64
	       " buffer_frames=%" PRIu64 "\n",
	       rate, (uint64_t)actual_period, (uint64_t)actual_buffer);
	return 0;
}

static int write_module_parameter(const char *name, const char *value)
{
	char path[160];
	FILE *file;

	if (snprintf(path, sizeof(path),
		     "/sys/module/snd_pcm_drain_test/parameters/%s", name) >=
	    (int)sizeof(path))
		return -ENAMETOOLONG;
	file = fopen(path, "w");
	if (!file)
		return -errno;
	if (fprintf(file, "%s\n", value) < 0) {
		int rc = -errno;

		fclose(file);
		return rc;
	}
	if (fclose(file) != 0)
		return -errno;
	return 0;
}

static int read_armed_counts(int counts[2])
{
	FILE *file = fopen(
		"/sys/module/snd_pcm_drain_test/parameters/armed_pointer_calls", "r");
	int rc;

	if (!file)
		return -errno;
	rc = fscanf(file, "%d,%d", &counts[0], &counts[1]);
	fclose(file);
	return rc == 2 ? 0 : -EINVAL;
}

static void close_handles(snd_pcm_t **first, snd_pcm_t **second)
{
	if (*second) {
		snd_pcm_close(*second);
		*second = NULL;
	}
	if (*first) {
		snd_pcm_close(*first);
		*first = NULL;
	}
}

static int reopen_and_prepare(const char *name0, const char *name1,
			      snd_pcm_t **first, snd_pcm_t **second)
{
	int rc;

	rc = snd_pcm_open(first, name0, SND_PCM_STREAM_CAPTURE,
			  SND_PCM_NONBLOCK);
	if (rc < 0)
		return rc;
	if ((rc = configure_capture(*first)) < 0 ||
	    (rc = snd_pcm_prepare(*first)) < 0)
		return rc;
	rc = snd_pcm_open(second, name1, SND_PCM_STREAM_CAPTURE,
			  SND_PCM_NONBLOCK);
	if (rc < 0)
		return rc;
	if ((rc = configure_capture(*second)) < 0 ||
	    (rc = snd_pcm_prepare(*second)) < 0)
		return rc;
	return 0;
}

static int run_linked_drain(const char *mode, const char *name0,
			    const char *name1, unsigned int wait_ms,
			    bool linked)
{
	snd_pcm_t *first = NULL;
	snd_pcm_t *second = NULL;
	snd_pcm_state_t state0;
	snd_pcm_state_t state1;
	int16_t sample = 0;
	snd_pcm_sframes_t read0;
	snd_pcm_sframes_t read1;
	int armed_counts[2] = { 0, 0 };
	bool inject_xrun = !strcmp(mode, "linked-xrun");
	bool parameters_armed = false;
	int rc;

	if ((rc = snd_pcm_open(&first, name0, SND_PCM_STREAM_CAPTURE,
			       SND_PCM_NONBLOCK)) < 0 ||
	    (rc = snd_pcm_open(&second, name1, SND_PCM_STREAM_CAPTURE,
			       SND_PCM_NONBLOCK)) < 0)
		goto out;
	if ((rc = configure_capture(first)) < 0 ||
	    (rc = configure_capture(second)) < 0 ||
	    (rc = snd_pcm_prepare(first)) < 0 ||
	    (rc = snd_pcm_prepare(second)) < 0)
		goto out;
	if (linked) {
		if ((rc = snd_pcm_link(first, second)) < 0) {
			fprintf(stderr, "snd_pcm_link: %s\n", snd_strerror(rc));
			goto out;
		}
	}
	printf("CAPTURE_READY mode=%s linkage=%s substreams=0,1\n", mode,
	       linked ? "linked" : "unlinked");
	if ((rc = snd_pcm_start(first)) < 0)
		goto out;
	printf("START_ISSUED=yes wait_ms=%u\n", wait_ms);
	fflush(stdout);
	if ((rc = sleep_ms(wait_ms)) < 0)
		goto out;

	/* Arm pointer tracing only after START; no ALSA query precedes DRAIN. */
	parameters_armed = true;
	if ((rc = write_module_parameter("fault_substream",
					 inject_xrun ?
					 STRINGIFY(XRUN_FAULT_SUBSTREAM) : "-1")) < 0 ||
	    (rc = write_module_parameter("trace_armed", "Y")) < 0)
		goto out;
	printf("POINTER_TRACE_ARMED fault_substream=%d\n",
	       inject_xrun ? XRUN_FAULT_SUBSTREAM : -1);
	fflush(stdout);
	rc = snd_pcm_drain(first);
	printf("DRAIN mode=%s rc=%d error=%s\n", mode, rc,
	       rc < 0 ? snd_strerror(rc) : "none");
	if ((!inject_xrun && rc != -EAGAIN) ||
	    (inject_xrun && rc != -EPIPE)) {
		if (rc >= 0)
			rc = -ENODATA;
		goto out;
	}

	if ((rc = read_armed_counts(armed_counts)) < 0)
		goto out;
	printf("ARMED_POINTER_COUNTS substream0=%d substream1=%d\n",
	       armed_counts[0], armed_counts[1]);
	if (armed_counts[0] < 1 ||
	    (linked && armed_counts[1] < 1) ||
	    (!linked && armed_counts[1] != 0)) {
		rc = -ENODATA;
		goto out;
	}

	/* Disable further injections before querying state or recovering. */
	if ((rc = write_module_parameter("trace_armed", "N")) < 0 ||
	    (rc = write_module_parameter("fault_substream", "-1")) < 0)
		goto out;
	state0 = snd_pcm_state(first);
	state1 = snd_pcm_state(second);
	printf("POST_DRAIN_STATE substream0=%s substream1=%s\n",
	       snd_pcm_state_name(state0), snd_pcm_state_name(state1));
	if (!inject_xrun) {
		if (state0 != SND_PCM_STATE_SETUP ||
		    state1 != (linked ? SND_PCM_STATE_SETUP :
				       SND_PCM_STATE_PREPARED)) {
			rc = -EUCLEAN;
			goto out;
		}
		read0 = snd_pcm_readi(first, &sample, 1);
		read1 = linked ? snd_pcm_readi(second, &sample, 1) : 0;
		printf("ZERO_POINTER_TERMINAL substream0=%" PRId64,
		       (int64_t)read0);
		if (linked)
			printf(" substream1=%" PRId64, (int64_t)read1);
		putchar('\n');
		if (read0 != -EBADFD || (linked && read1 != -EBADFD)) {
			rc = -EBADFD;
			goto out;
		}
		printf("ZERO_DRAIN_VERDICT=PASS linkage=%s state0=SETUP "
		       "state1=%s terminal=EBADFD pointers=%d,%d\n",
		       linked ? "linked" : "unlinked",
		       linked ? "SETUP" : "PREPARED", armed_counts[0],
		       armed_counts[1]);
		rc = 0;
		goto out;
	}

	printf("LINKED_XRUN_STATES substream0=%s substream1=%s\n",
	       snd_pcm_state_name(state0), snd_pcm_state_name(state1));
	if (state0 != SND_PCM_STATE_XRUN || state1 != SND_PCM_STATE_XRUN) {
		rc = -EUCLEAN;
		goto out;
	}
	rc = snd_pcm_prepare(first);
	printf("LINKED_XRUN_RECOVERY_PREPARE rc=%d error=%s\n", rc,
	       rc < 0 ? snd_strerror(rc) : "none");
	if (rc < 0)
		goto out;
	if ((rc = snd_pcm_unlink(first)) < 0)
		goto out;
	close_handles(&first, &second);
	rc = reopen_and_prepare(name0, name1, &first, &second);
	printf("LINKED_XRUN_RECOVERY_REOPEN_PREPARE rc=%d error=%s\n", rc,
	       rc < 0 ? snd_strerror(rc) : "none");
	if (rc < 0)
		goto out;
	printf("LINKED_XRUN_DRAIN_VERDICT=PASS direct_error=EPIPE "
	       "pointers=%d,%d recovery=prepare+reopen\n",
	       armed_counts[0], armed_counts[1]);
	rc = 0;
out:
	if (parameters_armed) {
		int cleanup_fault = write_module_parameter("fault_substream", "-1");
		int cleanup_trace = write_module_parameter("trace_armed", "N");

		if (rc == 0 && cleanup_fault < 0)
			rc = cleanup_fault;
		if (rc == 0 && cleanup_trace < 0)
			rc = cleanup_trace;
	}
	if (rc < 0)
		fprintf(stderr, "linked %s control failed: %s\n", mode,
			snd_strerror(rc));
	if (linked && first && second)
		(void)snd_pcm_unlink(first);
	close_handles(&first, &second);
	return rc < 0 ? EXIT_FAILURE : EXIT_SUCCESS;
}

static void usage(const char *program)
{
	fprintf(stderr,
		"usage: %s <zero-running|linked-xrun> <pcm-substream-0> "
		"<pcm-substream-1> [wait-ms=1] [linked|unlinked]\n", program);
}

int main(int argc, char **argv)
{
	unsigned int wait_ms = 1;
	bool linked = true;
	char *end;
	unsigned long parsed;

	if (argc < 4 || argc > 6 ||
	    (strcmp(argv[1], "zero-running") &&
	     strcmp(argv[1], "linked-xrun"))) {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	if (!strcmp(argv[1], "linked-xrun") && argc == 6 &&
	    strcmp(argv[5], "linked")) {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	if (argc >= 5) {
		errno = 0;
		parsed = strtoul(argv[4], &end, 10);
		if (errno || !*argv[4] || *end || parsed == 0 || parsed > UINT32_MAX) {
			usage(argv[0]);
			return EXIT_FAILURE;
		}
		wait_ms = (unsigned int)parsed;
	}
	if (argc == 6) {
		if (!strcmp(argv[5], "unlinked"))
			linked = false;
		else if (strcmp(argv[5], "linked")) {
			usage(argv[0]);
			return EXIT_FAILURE;
		}
	}
	if (!strcmp(argv[1], "linked-xrun") && !linked) {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	return run_linked_drain(argv[1], argv[2], argv[3], wait_ms, linked);
}

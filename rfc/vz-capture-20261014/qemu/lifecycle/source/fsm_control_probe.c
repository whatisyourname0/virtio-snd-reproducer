/* SPDX-License-Identifier: GPL-2.0-only */
#define _GNU_SOURCE

#include <alsa/asoundlib.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define RATE 48000U
#define PERIOD 1024U
#define BUFFER (PERIOD * 4U)
#define READ_FRAMES 8192U

static snd_pcm_t *pcm;
static pthread_mutex_t reader_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t reader_changed = PTHREAD_COND_INITIALIZER;
static atomic_int reader_done;
static atomic_long reader_tid;
static snd_pcm_sframes_t reader_first_rc;
static snd_pcm_sframes_t reader_rc;
static uint64_t reader_frames;
static int64_t reader_started_ms;

static int64_t monotonic_ms(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int fail_alsa(const char *stage, int rc)
{
	fprintf(stderr, "FSM_FAIL stage=%s rc=%d error=%s\n", stage, rc,
		snd_strerror(rc));
	return -1;
}

static int configure(void)
{
	snd_pcm_hw_params_t *hw;
	snd_pcm_sw_params_t *sw;
	snd_pcm_uframes_t period = PERIOD;
	snd_pcm_uframes_t buffer = BUFFER;
	snd_pcm_uframes_t actual_period;
	snd_pcm_uframes_t actual_buffer;
	snd_pcm_uframes_t avail_min = PERIOD;
	snd_pcm_uframes_t start_threshold = 1;
	unsigned int rate = RATE;
	int direction = 0;
	int rc;

	snd_pcm_hw_params_alloca(&hw);
	snd_pcm_sw_params_alloca(&sw);
	if ((rc = snd_pcm_hw_params_any(pcm, hw)) < 0)
		return fail_alsa("hw_params_any", rc);
	if ((rc = snd_pcm_hw_params_set_access(pcm, hw,
					       SND_PCM_ACCESS_RW_INTERLEAVED)) < 0)
		return fail_alsa("access", rc);
	if ((rc = snd_pcm_hw_params_set_format(pcm, hw,
					       SND_PCM_FORMAT_S16_LE)) < 0)
		return fail_alsa("format", rc);
	if ((rc = snd_pcm_hw_params_set_channels(pcm, hw, 1)) < 0)
		return fail_alsa("channels", rc);
	if ((rc = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate,
						  &direction)) < 0)
		return fail_alsa("rate", rc);
	if (rate != RATE)
		return fail_alsa("rate_mismatch", -EINVAL);
	direction = 0;
	if ((rc = snd_pcm_hw_params_set_period_size_near(pcm, hw, &period,
							 &direction)) < 0)
		return fail_alsa("period", rc);
	buffer = period * 4;
	if ((rc = snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer)) < 0)
		return fail_alsa("buffer", rc);
	if ((rc = snd_pcm_hw_params(pcm, hw)) < 0)
		return fail_alsa("hw_params", rc);
	if ((rc = snd_pcm_hw_params_get_period_size(hw, &actual_period,
						    &direction)) < 0)
		return fail_alsa("get_period", rc);
	if ((rc = snd_pcm_hw_params_get_buffer_size(hw, &actual_buffer)) < 0)
		return fail_alsa("get_buffer", rc);
	if (actual_period != PERIOD || actual_buffer != BUFFER) {
		fprintf(stderr,
			"FSM_FAIL stage=geometry period=%lu buffer=%lu expected_period=%u expected_buffer=%u\n",
			(unsigned long)actual_period, (unsigned long)actual_buffer,
			PERIOD, BUFFER);
		return fail_alsa("geometry", -EINVAL);
	}
	if ((rc = snd_pcm_sw_params_current(pcm, sw)) < 0)
		return fail_alsa("sw_params_current", rc);
	if ((rc = snd_pcm_sw_params_set_avail_min(pcm, sw, avail_min)) < 0)
		return fail_alsa("avail_min", rc);
	if ((rc = snd_pcm_sw_params_set_start_threshold(pcm, sw,
							 start_threshold)) < 0)
		return fail_alsa("start_threshold", rc);
	if ((rc = snd_pcm_sw_params(pcm, sw)) < 0)
		return fail_alsa("sw_params", rc);
	printf("FSM_CONFIG rate=%u period=%lu buffer=%lu\n", rate,
	       (unsigned long)actual_period, (unsigned long)actual_buffer);
	return 0;
}

static void *reader_thread(void *unused)
{
	int16_t *buffer = calloc(READ_FRAMES, sizeof(*buffer));

	(void)unused;
	atomic_store(&reader_tid, syscall(SYS_gettid));
	reader_started_ms = monotonic_ms();
	if (!buffer) {
		reader_rc = -ENOMEM;
		goto done;
	}
	for (unsigned int call = 0; ; call++) {
		snd_pcm_sframes_t rc = snd_pcm_readi(pcm, buffer, READ_FRAMES);

		if (!call) {
			reader_first_rc = rc;
			fprintf(stdout,
				"FSM_READER_FIRST_RETURN rc=%ld elapsed_ms=%lld\n",
				(long)rc, (long long)(monotonic_ms() - reader_started_ms));
			/*
			 * In the candidate sound/core/pcm_lib.c:2006-2028,
			 * wait_for_avail() checks XRUN before an expired wait. The
			 * stream lock decides which condition the first read observes:
			 * STOP/RELEASE may first return -EPIPE or -EIO. A retry after
			 * -EIO must observe XRUN and return -EPIPE.
			 */
			if (rc == -EIO) {
				usleep(300000);
				continue;
			}
		}
		if (rc < 0) {
			reader_rc = rc;
			break;
		}
		if (!rc) {
			reader_rc = -EIO;
			break;
		}
		reader_frames += rc;
	}
	fprintf(stdout,
		"FSM_READER_FINAL_RETURN rc=%ld first_rc=%ld frames=%llu elapsed_ms=%lld expected_epipe=%d\n",
		(long)reader_rc, (long)reader_first_rc,
		(unsigned long long)reader_frames,
		(long long)(monotonic_ms() - reader_started_ms),
		reader_rc == -EPIPE);
	free(buffer);
done:
	pthread_mutex_lock(&reader_lock);
	atomic_store(&reader_done, 1);
	pthread_cond_broadcast(&reader_changed);
	pthread_mutex_unlock(&reader_lock);
	return NULL;
}

static int reader_wchan(long tid, char *buffer, size_t size)
{
	char path[96];
	FILE *file;

	snprintf(path, sizeof(path), "/proc/self/task/%ld/wchan", tid);
	file = fopen(path, "r");
	if (!file)
		return -errno;
	if (!fgets(buffer, size, file)) {
		fclose(file);
		return -EIO;
	}
	fclose(file);
	buffer[strcspn(buffer, "\n")] = '\0';
	return 0;
}

static int wait_reader_blocked(long tid, unsigned int timeout_ms)
{
	unsigned int elapsed = 0;
	char wchan[128] = "unknown";

	while (elapsed < timeout_ms) {
		if (reader_wchan(tid, wchan, sizeof(wchan)) == 0 &&
		    !strcmp(wchan, "__snd_pcm_lib_xfer")) {
			printf("FSM_READER_BLOCKED tid=%ld wchan=%s\n",
			       tid, wchan);
			return 0;
		}
		usleep(10000);
		elapsed += 10;
	}
	fprintf(stderr, "FSM_FAIL stage=reader_not_blocked tid=%ld last_wchan=%s\n",
		tid, wchan);
	return -1;
}

static int wait_reader_done(unsigned int timeout_ms)
{
	struct timespec deadline;
	int rc = 0;

	clock_gettime(CLOCK_REALTIME, &deadline);
	deadline.tv_sec += timeout_ms / 1000;
	deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
	if (deadline.tv_nsec >= 1000000000L) {
		deadline.tv_sec++;
		deadline.tv_nsec -= 1000000000L;
	}
	pthread_mutex_lock(&reader_lock);
	while (!atomic_load(&reader_done) && !rc)
		rc = pthread_cond_timedwait(&reader_changed, &reader_lock,
					    &deadline);
	pthread_mutex_unlock(&reader_lock);
	if (!atomic_load(&reader_done)) {
		fprintf(stderr, "FSM_FAIL stage=reader_wake_timeout rc=%d\n", rc);
		return -1;
	}
	return reader_rc == -EPIPE ? 0 : -1;
}

int main(int argc, char **argv)
{
	const char *mode;
	pthread_t reader;
	long tid_snapshot;
	int rc;
	int pause_rc;
	int recovery_rc;
	int first_ok;
	int final_ok;
	int no_frames;
	int rejected;
	int xrun;
	int64_t pause_started;
	int64_t pause_finished;
	snd_pcm_state_t final_state;

	if (argc != 2) {
		fprintf(stderr, "usage: %s stop|release|prepare|start\n", argv[0]);
		return 2;
	}
	mode = argv[1];
	setvbuf(stdout, NULL, _IONBF, 0);
	setvbuf(stderr, NULL, _IONBF, 0);
	printf("FSM_TEST_BEGIN mode=%s\n", mode);
	rc = snd_pcm_open(&pcm, "hw:0,0", SND_PCM_STREAM_CAPTURE, 0);
	if (rc < 0) {
		fail_alsa("open", rc);
		return 1;
	}
	if (configure() < 0)
		goto failed;
	if ((rc = snd_pcm_prepare(pcm)) < 0) {
		fail_alsa("prepare", rc);
		goto failed;
	}
	if ((rc = snd_pcm_start(pcm)) < 0) {
		fail_alsa("start", rc);
		goto failed;
	}
	if ((rc = pthread_create(&reader, NULL, reader_thread, NULL)) != 0) {
		fprintf(stderr, "FSM_FAIL stage=reader_create rc=%d\n", rc);
		goto failed;
	}
	for (unsigned int elapsed = 0; elapsed < 1000 &&
	     !atomic_load(&reader_tid); elapsed += 1)
		usleep(1000);
	tid_snapshot = atomic_load(&reader_tid);
	if (tid_snapshot <= 0 || wait_reader_blocked(tid_snapshot, 5000) < 0)
		goto failed_reader;

	if (!strcmp(mode, "stop") || !strcmp(mode, "release")) {
		pause_started = monotonic_ms();
		pause_rc = snd_pcm_pause(pcm, 1);
		pause_finished = monotonic_ms();
		printf("FSM_PAUSE_PUSH_RETURN rc=%d elapsed_ms=%lld expected_timeout=%d\n",
		       pause_rc, (long long)(pause_finished - pause_started),
		       pause_rc == -ETIMEDOUT);
	} else if (!strcmp(mode, "prepare") || !strcmp(mode, "start")) {
		pause_rc = snd_pcm_pause(pcm, 1);
		printf("FSM_PAUSE_PUSH_RETURN rc=%d expected_success=%d\n",
		       pause_rc, pause_rc == 0);
		if (pause_rc < 0)
			goto failed_reader;
		{
			unsigned int elapsed = 0;
			char wchan[128] = "unknown";

			while (elapsed < 3000) {
				if (reader_wchan(tid_snapshot, wchan, sizeof(wchan)) == 0 &&
				    !strcmp(wchan, "__snd_pcm_lib_xfer"))
					break;
				if (atomic_load(&reader_done))
					break;
				usleep(10000);
				elapsed += 10;
			}
			if (strcmp(wchan, "__snd_pcm_lib_xfer")) {
				fprintf(stderr,
					"FSM_FAIL stage=paused_reader_not_blocked wchan=%s\n",
					wchan);
				goto failed_reader;
			}
			printf("FSM_READER_BLOCKED_PAUSED tid=%ld wchan=%s\n",
			       tid_snapshot, wchan);
		}
		pause_started = monotonic_ms();
		pause_rc = snd_pcm_pause(pcm, 0);
		pause_finished = monotonic_ms();
		printf("FSM_PAUSE_RELEASE_RETURN rc=%d elapsed_ms=%lld expected_timeout=%d\n",
		       pause_rc, (long long)(pause_finished - pause_started),
		       pause_rc == -ETIMEDOUT);
	} else {
		fprintf(stderr, "FSM_FAIL stage=invalid_mode mode=%s\n", mode);
		goto failed_reader;
	}
	if (pause_rc != -ETIMEDOUT || pause_finished < pause_started ||
	    pause_finished - pause_started < 800 ||
	    pause_finished - pause_started > 2500) {
		fprintf(stderr, "FSM_FAIL stage=timeout_result rc=%d\n", pause_rc);
		goto failed_reader;
	}
	if (wait_reader_done(2000) < 0)
		goto failed_reader;
	pthread_join(reader, NULL);
	final_state = snd_pcm_state(pcm);
	if (!strcmp(mode, "stop") || !strcmp(mode, "release"))
		first_ok = reader_first_rc == -EIO || reader_first_rc == -EPIPE;
	else
		first_ok = reader_first_rc == -EPIPE;
	final_ok = reader_rc == -EPIPE;
	no_frames = reader_frames == 0;
	xrun = final_state == SND_PCM_STATE_XRUN;
	printf("FSM_READER_ASSERT first_rc=%ld final_rc=%ld frames=%llu state=%s "
	       "first_ok=%d final_ok=%d no_frames=%d xrun=%d\n",
	       (long)reader_first_rc, (long)reader_rc,
	       (unsigned long long)reader_frames,
	       snd_pcm_state_name(final_state), first_ok, final_ok, no_frames, xrun);
	if (!first_ok || !final_ok || !no_frames || !xrun) {
		fprintf(stderr, "FSM_FAIL stage=reader_result\n");
		goto failed_reader;
	}

	/* Subsequent ALSA recovery must fail closed until reprobe. */
	recovery_rc = snd_pcm_prepare(pcm);
	rejected = recovery_rc == -EIO;
	printf("FSM_PREPARE_WITHOUT_REPROBE rc=%d rejected=%d expected_eio=%d\n",
	       recovery_rc, rejected, rejected);
	if (!rejected) {
		fprintf(stderr, "FSM_FAIL stage=unsafe_prepare_recovery\n");
		goto failed_reader;
	}

	if ((rc = snd_pcm_close(pcm)) < 0)
		printf("FSM_CLOSE_AFTER_FAILURE rc=%d\n", rc);
	pcm = NULL;
	printf("FSM_CONTROL_CASE_PASS mode=%s\n", mode);
	return 0;

failed_reader:
	fflush(stdout);
	fflush(stderr);
	exit(1);
failed:
	if (pcm)
		snd_pcm_close(pcm);
	return 1;
}

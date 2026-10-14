/* SPDX-License-Identifier: GPL-2.0-only */
#define _POSIX_C_SOURCE 200809L

#include <alsa/asoundlib.h>
#include <alloca.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define FNV1A64_OFFSET UINT64_C(14695981039346656037)
#define FNV1A64_PRIME UINT64_C(1099511628211)

enum capture_action {
	ACTION_DRAIN,
	ACTION_DROP,
};

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

static uint64_t hash_bytes(uint64_t hash, const unsigned char *bytes,
			   size_t length)
{
	size_t i;

	for (i = 0; i < length; i++) {
		hash ^= bytes[i];
		hash *= FNV1A64_PRIME;
	}
	return hash;
}

static int parse_decimal_u64(const char *text, uint64_t *value)
{
	const char *cursor;
	char *end;
	unsigned long long parsed;

	if (!*text)
		return -EINVAL;
	for (cursor = text; *cursor; cursor++) {
		if (*cursor < '0' || *cursor > '9')
			return -EINVAL;
	}

	errno = 0;
	parsed = strtoull(text, &end, 10);
	if (errno || *end)
		return -EINVAL;
	*value = (uint64_t)parsed;
	return 0;
}

static int parse_fnv1a64(const char *text, uint64_t *value)
{
	const char *cursor;
	char *end;
	unsigned long long parsed;

	if (strlen(text) != 16)
		return -EINVAL;
	for (cursor = text; *cursor; cursor++) {
		if (!((*cursor >= '0' && *cursor <= '9') ||
		      (*cursor >= 'a' && *cursor <= 'f') ||
		      (*cursor >= 'A' && *cursor <= 'F')))
			return -EINVAL;
	}

	errno = 0;
	parsed = strtoull(text, &end, 16);
	if (errno || *end)
		return -EINVAL;
	*value = (uint64_t)parsed;
	return 0;
}

static int configure_capture(snd_pcm_t *pcm,
			     snd_pcm_uframes_t requested_period,
			     snd_pcm_uframes_t *period_out,
			     snd_pcm_uframes_t *buffer_out)
{
	snd_pcm_hw_params_t *hw;
	snd_pcm_sw_params_t *sw;
	snd_pcm_uframes_t period = requested_period;
	snd_pcm_uframes_t buffer = requested_period * 4;
	snd_pcm_uframes_t actual_period;
	snd_pcm_uframes_t actual_buffer;
	snd_pcm_uframes_t avail_min = 1;
	snd_pcm_uframes_t start_threshold = 1;
	snd_pcm_uframes_t stop_threshold;
	unsigned int rate = 48000;
	int direction = 0;
	int rc;

	snd_pcm_hw_params_alloca(&hw);
	snd_pcm_sw_params_alloca(&sw);

	if ((rc = snd_pcm_hw_params_any(pcm, hw)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params_set_access(pcm, hw,
					       SND_PCM_ACCESS_RW_INTERLEAVED)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params_set_format(pcm, hw,
					       SND_PCM_FORMAT_S16_LE)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params_set_channels(pcm, hw, 1)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate,
						 &direction)) < 0)
		return rc;
	direction = 0;
	if ((rc = snd_pcm_hw_params_set_period_size_near(pcm, hw, &period,
							 &direction)) < 0)
		return rc;
	buffer = period * 4;
	if ((rc = snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params(pcm, hw)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params_get_period_size(hw, &actual_period,
						    &direction)) < 0)
		return rc;
	if ((rc = snd_pcm_hw_params_get_buffer_size(hw, &actual_buffer)) < 0)
		return rc;

	if ((rc = snd_pcm_sw_params_current(pcm, sw)) < 0)
		return rc;
	if ((rc = snd_pcm_sw_params_set_avail_min(pcm, sw, avail_min)) < 0)
		return rc;
	if ((rc = snd_pcm_sw_params_set_start_threshold(pcm, sw,
							 start_threshold)) < 0)
		return rc;
	stop_threshold = actual_buffer;
	if ((rc = snd_pcm_sw_params_set_stop_threshold(pcm, sw,
							 stop_threshold)) < 0)
		return rc;
	if ((rc = snd_pcm_sw_params(pcm, sw)) < 0)
		return rc;

	*buffer_out = actual_buffer;
	*period_out = actual_period;
	printf("CONFIG rate=%u format=S16_LE channels=1 period_frames=%" PRIu64
	       " buffer_frames=%" PRIu64 "\n",
	       rate, (uint64_t)actual_period, (uint64_t)actual_buffer);
	return 0;
}

static int drain_and_verify(snd_pcm_t *pcm, uint64_t expected_frames,
			    uint64_t expected_hash,
			    snd_pcm_uframes_t buffer_frames)
{
	size_t capacity = (size_t)buffer_frames;
	int16_t *samples;
	uint64_t total_frames = 0;
	uint64_t hash = FNV1A64_OFFSET;
	unsigned int attempts;
	const char *end_reason = "read-limit";
	bool reached_eof = false;
	bool saw_extra_frames = false;
	int rc;

	samples = calloc(capacity, sizeof(*samples));
	if (!samples)
		return -ENOMEM;

	rc = snd_pcm_drain(pcm);
	printf("DRAIN rc=%d error=%s\n", rc,
	       rc < 0 ? snd_strerror(rc) : "none");
	if (rc < 0 && rc != -EAGAIN) {
		free(samples);
		return rc;
	}
	for (attempts = 0; attempts < 8192; attempts++) {
		snd_pcm_uframes_t request;
		snd_pcm_sframes_t got;

		if (total_frames < expected_frames) {
			uint64_t remaining = expected_frames - total_frames;
			request = remaining > buffer_frames ? buffer_frames :
				(snd_pcm_uframes_t)remaining;
		} else {
			request = 1;
		}

		got = snd_pcm_readi(pcm, samples, request);
		if (got > 0) {
			hash = hash_bytes(hash, (const unsigned char *)samples,
					  (size_t)got * sizeof(*samples));
			total_frames += (uint64_t)got;
			if (total_frames > expected_frames) {
				end_reason = "extra-frames";
				saw_extra_frames = true;
				printf("DRAIN_READ extra_frames=%" PRIu64 "\n",
				       total_frames - expected_frames);
				break;
			}
			continue;
		}
		if (got == 0) {
			end_reason = "zero-read";
			reached_eof = true;
			break;
		}
		if (got == -EAGAIN || got == -EBADFD) {
			end_reason = got == -EAGAIN ? "EAGAIN" : "EBADFD";
			if (expected_frames == 0 && got == -EBADFD)
				reached_eof = true;
			break;
		}

		printf("DRAIN_READ error_rc=%" PRId64 " error=%s\n",
		       (int64_t)got, snd_strerror((int)got));
		free(samples);
		return (int)got;
	}

	if (attempts == 8192 && !reached_eof && !saw_extra_frames)
		end_reason = "read-limit";
	printf("DRAIN_READ_END reason=%s frames=%" PRIu64
	       " fnv1a64=%016" PRIx64 "\n",
	       end_reason, total_frames, hash);
	printf("DRAIN_EXPECT frames=%" PRIu64 " fnv1a64=%016" PRIx64 "\n",
	       expected_frames, expected_hash);
	free(samples);

	if (saw_extra_frames)
		return -EOVERFLOW;
	if (!reached_eof)
		return -ETIMEDOUT;
	if (total_frames != expected_frames || hash != expected_hash)
		return -EBADMSG;
	return 0;
}

static int drop_and_verify(snd_pcm_t *pcm, uint64_t expected_hash)
{
	int16_t sample = 0;
	snd_pcm_sframes_t got;
	uint64_t hash = FNV1A64_OFFSET;
	int rc;

	rc = snd_pcm_drop(pcm);
	printf("DROP rc=%d error=%s\n", rc,
	       rc < 0 ? snd_strerror(rc) : "none");
	if (rc < 0)
		return rc;

	got = snd_pcm_readi(pcm, &sample, 1);
	if (got > 0) {
		hash = hash_bytes(hash, (const unsigned char *)&sample,
				  (size_t)got * sizeof(sample));
		printf("DROP_READ frames=%" PRId64 " fnv1a64=%016" PRIx64 "\n",
		       (int64_t)got, hash);
		return -EOVERFLOW;
	}
	printf("DROP_READ rc=%" PRId64 " error=%s frames=0 fnv1a64=%016" PRIx64
	       "\n",
	       (int64_t)got, got < 0 ? snd_strerror((int)got) : "none", hash);
	if (got != -EBADFD || hash != expected_hash)
		return got < 0 ? (int)got : -EBADMSG;
	return 0;
}

static void usage(const char *program)
{
	fprintf(stderr,
		"usage: %s <pcm> <drain|drop> <expected-frames> "
		"<expected-fnv1a64-16hex> [period-frames=2048] [wait-ms=120]\n",
		program);
}

int main(int argc, char **argv)
{
	const char *pcm_name;
	enum capture_action action;
	uint64_t expected_frames;
	uint64_t expected_hash;
	uint64_t parsed_period;
	unsigned int period_arg = 2048;
	unsigned int wait_ms = 120;
	snd_pcm_t *pcm = NULL;
	snd_pcm_uframes_t period_frames;
	snd_pcm_uframes_t buffer_frames;
	int rc;

	if (argc < 5 || argc > 7) {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	pcm_name = argv[1];
	if (!strcmp(argv[2], "drain"))
		action = ACTION_DRAIN;
	else if (!strcmp(argv[2], "drop"))
		action = ACTION_DROP;
	else {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	if (parse_decimal_u64(argv[3], &expected_frames) < 0 ||
	    parse_fnv1a64(argv[4], &expected_hash) < 0) {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	if (argc >= 6) {
		if (parse_decimal_u64(argv[5], &parsed_period) < 0 ||
		    parsed_period > UINT32_MAX) {
			usage(argv[0]);
			return EXIT_FAILURE;
		}
		period_arg = (unsigned int)parsed_period;
	}
	if (argc >= 7) {
		uint64_t parsed_wait;

		if (parse_decimal_u64(argv[6], &parsed_wait) < 0 ||
		    parsed_wait == 0 || parsed_wait > UINT32_MAX) {
			usage(argv[0]);
			return EXIT_FAILURE;
		}
		wait_ms = (unsigned int)parsed_wait;
	}
	if (!period_arg || period_arg > UINT32_MAX / 4 ||
	    expected_frames > UINT32_MAX * UINT64_C(4)) {
		fprintf(stderr, "period or expected frame count is out of range\n");
		return EXIT_FAILURE;
	}
	if (action == ACTION_DROP && expected_frames != 0) {
		fprintf(stderr, "drop requires expected-frames=0\n");
		return EXIT_FAILURE;
	}
	if (action == ACTION_DROP && expected_hash != FNV1A64_OFFSET) {
		fprintf(stderr, "drop requires the empty-stream FNV-1a64 hash\n");
		return EXIT_FAILURE;
	}

	rc = snd_pcm_open(&pcm, pcm_name, SND_PCM_STREAM_CAPTURE,
			  SND_PCM_NONBLOCK);
	if (rc < 0) {
		fprintf(stderr, "snd_pcm_open(%s): %s\n", pcm_name,
			snd_strerror(rc));
		goto out;
	}
	if ((rc = configure_capture(pcm, period_arg, &period_frames,
				    &buffer_frames)) < 0) {
		fprintf(stderr, "configure: %s\n", snd_strerror(rc));
		goto out;
	}
	if (expected_frames > buffer_frames) {
		fprintf(stderr, "expected frame count exceeds capture buffer\n");
		rc = -EINVAL;
		goto out;
	}
	if ((rc = snd_pcm_prepare(pcm)) < 0) {
		fprintf(stderr, "snd_pcm_prepare: %s\n", snd_strerror(rc));
		goto out;
	}
	if ((rc = snd_pcm_start(pcm)) < 0) {
		fprintf(stderr, "snd_pcm_start: %s\n", snd_strerror(rc));
		goto out;
	}
	printf("START issued; wait_ms=%u; next PCM operation is %s\n",
	       wait_ms, action == ACTION_DRAIN ? "snd_pcm_drain" : "snd_pcm_drop");
	fflush(stdout);
	if ((rc = sleep_ms(wait_ms)) < 0) {
		fprintf(stderr, "sleep: %s\n", strerror(-rc));
		goto out;
	}

	/* No state, status, pointer, or availability query between START and here. */
	if (action == ACTION_DRAIN)
		rc = drain_and_verify(pcm, expected_frames, expected_hash,
				     buffer_frames);
	else
		rc = drop_and_verify(pcm, expected_hash);
	if (rc < 0) {
		fprintf(stderr, "%s verification failed: %s\n",
			action == ACTION_DRAIN ? "drain" : "drop", snd_strerror(rc));
		goto out;
	}
	printf("DRAIN_NO_QUERY_VERDICT=PASS action=%s frames=%" PRIu64
	       " fnv1a64=%016" PRIx64 "\n",
	       action == ACTION_DRAIN ? "drain" : "drop", expected_frames,
	       expected_hash);
	rc = 0;
out:
	if (pcm)
		snd_pcm_close(pcm);
	return rc < 0 ? EXIT_FAILURE : EXIT_SUCCESS;
}

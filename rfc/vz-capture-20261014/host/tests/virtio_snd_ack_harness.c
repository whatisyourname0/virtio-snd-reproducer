// SPDX-License-Identifier: GPL-2.0-only
/* Host fixture for exact source-extracted virtio PCM ACK functions. */

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifndef EBADFD
#define EBADFD 77
#endif

typedef uint64_t snd_pcm_uframes_t;
typedef int64_t snd_pcm_sframes_t;
typedef uint64_t u64;

struct snd_pcm_substream;

/* EXTRACTED_VIRTSND_ACK_TYPES */

struct snd_pcm_control {
	snd_pcm_uframes_t appl_ptr;
};

struct snd_pcm_runtime {
	struct snd_pcm_control *control;
	snd_pcm_uframes_t boundary;
	snd_pcm_uframes_t buffer_size;
	unsigned int frame_bytes;
	void *dma_area;
	bool stop_operating;
};

struct work_struct { unsigned int schedules; };

struct snd_pcm_substream {
	struct snd_pcm_runtime *runtime;
	void *private_data;
};

struct device {
	int unused;
};

struct virtio_device {
	struct device dev;
};

struct virtio_snd {
	struct virtio_device *vdev;
};

struct virtio_snd_queue {
	int lock;
};

struct virtio_pcm_substream {
	struct virtio_snd *snd;
	struct snd_pcm_substream *substream;
	struct snd_pcm_indirect pcm_indirect;
	struct virtio_snd_queue queue;
	int lock;
	unsigned int sid;
	bool xfer_enabled;
	bool stopped;
	bool xfer_xrun;
	unsigned int pending;
	int send_rc[8];
	unsigned int send_rc_count;
	unsigned int send_calls;
	size_t buffer_bytes;
	size_t hw_ptr;
	size_t capture_ready;
	u64 capture_copied_total;
	u64 capture_acked_total;
	u64 capture_pointer_seen_total;
	u64 capture_callback_snapshot;
	bool capture_callback_snapshot_active;
	unsigned int capture_send_calls;
	int capture_send_rc;
	bool capture_send_partial_enqueue;
	bool capture_staging;
	bool runtime_active;
	struct work_struct elapsed_period;
};

static struct virtio_device test_vdev;
static struct virtio_snd test_snd = { .vdev = &test_vdev };

#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define scoped_guard(_type, _lock) \
	for (int _guard_once = ((void)(_lock), 1); _guard_once; _guard_once = 0)
#define guard(_type) mock_guard
#define dev_err(_dev, _fmt, ...) fprintf(stderr, _fmt, ##__VA_ARGS__)
#define check_add_overflow(a, b, result) \
	__builtin_add_overflow((a), (b), (result))

static void mock_guard(void *lock)
{
	(void)lock;
}

static void schedule_work(struct work_struct *work)
{
	work->schedules++;
}

static inline struct virtio_pcm_substream *
snd_pcm_substream_chip(struct snd_pcm_substream *substream)
{
	return substream->private_data;
}

static inline struct virtio_snd_queue *
virtsnd_pcm_queue(struct virtio_pcm_substream *vss)
{
	return &vss->queue;
}

static inline unsigned int
frames_to_bytes(struct snd_pcm_runtime *runtime, snd_pcm_uframes_t frames)
{
	return runtime->frame_bytes * frames;
}

static int virtsnd_pcm_msg_send(struct virtio_pcm_substream *vss,
				unsigned long offset, unsigned long bytes)
{
	unsigned int call = vss->send_calls++;
	int rc = call < vss->send_rc_count ? vss->send_rc[call] : 0;

	(void)offset;
	(void)bytes;
	if (!rc)
		vss->pending++;
	return rc;
}

/* Model capture request submission, including a request queued before failure. */
static int virtsnd_pcm_capture_send(struct virtio_pcm_substream *vss)
{
	vss->capture_send_calls++;
	if (vss->capture_send_partial_enqueue)
		vss->pending++;
	return vss->capture_send_rc;
}

/* EXTRACTED_VIRTSND_ACK_FUNCTIONS */

static int failures;
static unsigned int assertions;

static void check(bool condition, const char *label)
{
	assertions++;
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", label);
		failures++;
	}
}

static void setup(struct virtio_pcm_substream *vss,
		  struct snd_pcm_substream *substream,
		  struct snd_pcm_runtime *runtime,
		  struct snd_pcm_control *control)
{
	memset(vss, 0, sizeof(*vss));
	memset(substream, 0, sizeof(*substream));
	memset(runtime, 0, sizeof(*runtime));
	memset(control, 0, sizeof(*control));
	vss->snd = &test_snd;
	vss->substream = substream;
	vss->sid = 17;
	substream->runtime = runtime;
	substream->private_data = vss;
	runtime->control = control;
	runtime->boundary = 64;
	runtime->buffer_size = 8;
	runtime->frame_bytes = 1;
	runtime->dma_area = runtime;
	vss->xfer_enabled = true;
	vss->pcm_indirect.hw_buffer_size = 8;
	vss->pcm_indirect.hw_queue_size = 8;
	vss->pcm_indirect.sw_buffer_size = 8;
}

static void setup_partial_playback(struct virtio_pcm_substream *vss,
				   struct snd_pcm_substream *substream,
				   struct snd_pcm_runtime *runtime,
				   struct snd_pcm_control *control)
{
	setup(vss, substream, runtime, control);
	vss->pcm_indirect.hw_data = 6;
	vss->pcm_indirect.sw_data = 6;
	control->appl_ptr = 12;
	vss->send_rc[0] = 0;
	vss->send_rc[1] = -ENOMEM;
	vss->send_rc_count = 2;
}

static void setup_partial_capture(struct virtio_pcm_substream *vss,
				  struct snd_pcm_substream *substream,
				  struct snd_pcm_runtime *runtime,
				  struct snd_pcm_control *control)
{
	setup(vss, substream, runtime, control);
	vss->pcm_indirect.hw_data = 6;
	vss->pcm_indirect.sw_data = 6;
	vss->pcm_indirect.hw_ready = 8;
	vss->send_rc[0] = 0;
	vss->send_rc[1] = -ENOMEM;
	vss->send_rc_count = 2;
}

static void setup_first_failure_playback(struct virtio_pcm_substream *vss,
					 struct snd_pcm_substream *substream,
					 struct snd_pcm_runtime *runtime,
					 struct snd_pcm_control *control)
{
	setup(vss, substream, runtime, control);
	vss->pcm_indirect.hw_data = 6;
	vss->pcm_indirect.sw_data = 4;
	control->appl_ptr = 12;
	vss->send_rc[0] = -ENOMEM;
	vss->send_rc_count = 1;
}

static void setup_first_failure_capture(struct virtio_pcm_substream *vss,
					struct snd_pcm_substream *substream,
					struct snd_pcm_runtime *runtime,
					struct snd_pcm_control *control)
{
	setup(vss, substream, runtime, control);
	vss->pcm_indirect.hw_data = 6;
	vss->pcm_indirect.sw_data = 4;
	vss->pcm_indirect.hw_ready = 8;
	vss->send_rc[0] = -ENOMEM;
	vss->send_rc_count = 1;
}

static void recover_after_ack_failure(struct virtio_pcm_substream *vss,
				      bool playback)
{
	/* Model successful RELEASE completion, then prepare and trigger start. */
	check(vss->stopped && vss->substream->runtime->stop_operating,
	      "ACK failure requests stream cleanup");
	vss->pending = 0;
	vss->stopped = false;
	vss->substream->runtime->stop_operating = false;
	memset(&vss->pcm_indirect, 0, sizeof(vss->pcm_indirect));
	vss->pcm_indirect.hw_buffer_size = 8;
	vss->pcm_indirect.hw_queue_size = 8;
	vss->pcm_indirect.sw_buffer_size = 8;
	vss->send_calls = 0;
	vss->send_rc_count = 0;
	vss->xfer_enabled = true;
	if (playback)
		vss->substream->runtime->control->appl_ptr = 4;
	else
		vss->pcm_indirect.hw_ready = 4;
}

static void test_baseline_failure(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;
	struct snd_pcm_indirect before;

	setup_partial_playback(&vss, &substream, &runtime, &control);
	before = vss.pcm_indirect;
	check(virtsnd_pcm_pb_ack(&substream) == -EPIPE,
	      "baseline playback ACK reports send failure");
	check(!memcmp(&before, &vss.pcm_indirect, sizeof(before)),
	      "baseline playback leaves transfer state unchanged");

	setup_partial_capture(&vss, &substream, &runtime, &control);
	before = vss.pcm_indirect;
	check(virtsnd_pcm_cp_ack(&substream) == -EPIPE,
	      "baseline capture ACK reports send failure");
	check(!memcmp(&before, &vss.pcm_indirect, sizeof(before)),
	      "baseline capture leaves transfer state unchanged");
}

static void test_playback_failure_and_recovery(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;
	struct snd_pcm_indirect before;

	setup_partial_playback(&vss, &substream, &runtime, &control);
	before = vss.pcm_indirect;
	check(virtsnd_pcm_pb_ack(&substream) == -EPIPE,
	      "playback ACK surfaces send failure as XRUN");
	check(!memcmp(&before, &vss.pcm_indirect, sizeof(before)),
	      "playback state is not committed on partial send");
	check(vss.stopped && !vss.xfer_enabled && runtime.stop_operating,
	      "playback failure stops transfer and schedules RELEASE");
	check(vss.send_calls == 2 && vss.pending == 1,
	      "playback stops after failed chunk and tracks earlier queued chunk");
	check(virtsnd_pcm_pb_ack(&substream) == -EPIPE && vss.send_calls == 2,
	      "stopped playback ACK cannot retry before RELEASE");

	recover_after_ack_failure(&vss, true);
	check(virtsnd_pcm_pb_ack(&substream) == 0,
	      "playback ACK succeeds after modeled RELEASE and prepare recovery");
	check(vss.pending == 1 && !vss.stopped,
	      "playback restart queues data in the recovered state");
}

static void test_capture_failure_and_recovery(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;
	struct snd_pcm_indirect before;

	setup_partial_capture(&vss, &substream, &runtime, &control);
	before = vss.pcm_indirect;
	check(virtsnd_pcm_cp_ack(&substream) == -EPIPE,
	      "capture ACK surfaces send failure as XRUN");
	check(!memcmp(&before, &vss.pcm_indirect, sizeof(before)),
	      "capture state is not committed on partial send");
	check(vss.stopped && !vss.xfer_enabled && runtime.stop_operating,
	      "capture failure stops transfer and schedules RELEASE");
	check(vss.send_calls == 2 && vss.pending == 1,
	      "capture stops after failed chunk and tracks earlier queued chunk");
	check(virtsnd_pcm_cp_ack(&substream) == -EPIPE && vss.send_calls == 2,
	      "stopped capture ACK cannot retry before RELEASE");

	recover_after_ack_failure(&vss, false);
	check(virtsnd_pcm_cp_ack(&substream) == 0,
	      "capture ACK succeeds after modeled RELEASE and prepare recovery");
	check(vss.pending == 1 && !vss.stopped,
	      "capture restart queues data in the recovered state");
}

static void test_callback_suppresses_sends_after_error(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;
	struct snd_pcm_indirect before;

	setup_first_failure_playback(&vss, &substream, &runtime, &control);
	before = vss.pcm_indirect;
	check(virtsnd_pcm_pb_ack(&substream) == -EPIPE,
	      "playback reports its first send error");
	check(vss.send_calls == 1 && vss.pending == 0,
	      "playback callback suppresses later sends after error");
	check(!memcmp(&before, &vss.pcm_indirect, sizeof(before)),
	      "playback discards helper bookkeeping after first send error");

	setup_first_failure_capture(&vss, &substream, &runtime, &control);
	before = vss.pcm_indirect;
	check(virtsnd_pcm_cp_ack(&substream) == -EPIPE,
	      "capture reports its first send error");
	check(vss.send_calls == 1 && vss.pending == 0,
	      "capture callback suppresses later sends after error");
	check(!memcmp(&before, &vss.pcm_indirect, sizeof(before)),
	      "capture discards helper bookkeeping after first send error");
}

static void test_64_bit_appl_ptr_wrap(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;

	setup(&vss, &substream, &runtime, &control);
	vss.pcm_indirect.appl_ptr = 63;
	control.appl_ptr = 1;
	check(virtsnd_pcm_pb_ack(&substream) == 0,
	      "playback accepts 64-bit appl_ptr wrap at ALSA boundary");
	check(vss.pcm_indirect.appl_ptr == 1 && vss.pending == 1,
	      "playback advances wrapped appl_ptr by two frames");

	setup(&vss, &substream, &runtime, &control);
	vss.pcm_indirect.appl_ptr = 63;
	vss.pcm_indirect.hw_ready = 2;
	vss.pcm_indirect.sw_ready = 4;
	control.appl_ptr = 1;
	check(virtsnd_pcm_cp_ack(&substream) == 0,
	      "capture accepts 64-bit appl_ptr wrap at ALSA boundary");
	check(vss.pcm_indirect.appl_ptr == 1 && vss.pending == 1,
	      "capture advances wrapped appl_ptr by two frames");
}

static void setup_staged_capture(struct virtio_pcm_substream *vss,
				 struct snd_pcm_substream *substream,
				 struct snd_pcm_runtime *runtime,
				 struct snd_pcm_control *control)
{
	setup(vss, substream, runtime, control);
	vss->capture_staging = true;
	vss->runtime_active = true;
	vss->buffer_bytes = 24;
	vss->capture_ready = 20;
	vss->capture_copied_total = 26;
	vss->capture_acked_total = 6;
	vss->capture_pointer_seen_total = 22;
	vss->hw_ptr = 2;
	runtime->frame_bytes = 2;
	vss->pcm_indirect.appl_ptr = 3;
	control->appl_ptr = 5;
}

static void test_staged_capture_commit(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;

	setup_staged_capture(&vss, &substream, &runtime, &control);
	check(virtsnd_pcm_cp_ack(&substream) == 0,
	      "staged capture ACK succeeds after queue refill");
	check(vss.capture_ready == 16 && vss.pcm_indirect.appl_ptr == 5,
	      "staged capture commits ready bytes and appl_ptr together");
	check(vss.capture_acked_total == 10 &&
	      vss.capture_copied_total - vss.capture_acked_total ==
		vss.capture_ready,
	      "staged capture keeps monotonic occupancy counters consistent");
	check(vss.capture_send_calls == 1 && !vss.stopped &&
	      !runtime.stop_operating,
	      "successful staged capture remains active");
}

static void test_staged_capture_partial_enqueue_failure(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;

	setup_staged_capture(&vss, &substream, &runtime, &control);
	vss.capture_send_rc = -ENOMEM;
	vss.capture_send_partial_enqueue = true;
	check(virtsnd_pcm_cp_ack(&substream) == -ENOMEM,
	      "staged capture returns the queue refill failure");
	check(vss.capture_ready == 16 && vss.pcm_indirect.appl_ptr == 5 &&
	      vss.capture_acked_total == 10,
	      "staged capture commits consumed bytes before refill failure");
	check(vss.pending == 1 && vss.capture_send_calls == 1,
	      "partially enqueued capture request remains pending for RELEASE");
	check(vss.xfer_xrun && vss.elapsed_period.schedules == 1,
	      "staged send failure marks XRUN and schedules ALSA notification");
	check(virtsnd_pcm_cp_ack(&substream) == -EPIPE &&
	      vss.capture_send_calls == 1 && vss.capture_ready == 16 &&
	      vss.pcm_indirect.appl_ptr == 5 && vss.capture_acked_total == 10,
	      "stopped staged capture cannot retry or commit before RELEASE");
}

static void test_staged_capture_drain_ack_after_stop(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct snd_pcm_control control;

	setup_staged_capture(&vss, &substream, &runtime, &control);
	vss.stopped = true;
	vss.xfer_enabled = false;
	check(virtsnd_pcm_cp_ack(&substream) == 0,
	      "staged drain ACK consumes already-captured residual bytes");
	check(vss.capture_send_calls == 0 && vss.capture_ready == 16 &&
	      vss.pcm_indirect.appl_ptr == 5 && vss.capture_acked_total == 10,
	      "staged drain ACK advances counters without refilling stopped stream");
	check(vss.stopped && !vss.xfer_enabled,
	      "staged drain ACK preserves stopped transfer state");
}

int main(void)
{
#ifdef BASELINE
	test_baseline_failure();
	if (failures) {
		fprintf(stderr, "BASELINE_EXPECTED_FAILURES=%d\n", failures);
		return 10;
	}
	puts("baseline unexpectedly passed");
	return 0;
#else
	test_playback_failure_and_recovery();
	test_capture_failure_and_recovery();
	test_callback_suppresses_sends_after_error();
	test_64_bit_appl_ptr_wrap();
	test_staged_capture_commit();
	test_staged_capture_partial_enqueue_failure();
	test_staged_capture_drain_ack_after_stop();
	if (failures)
		return 1;
	printf("ACK transfer, fail-stop, and recovery checks passed assertions=%u\n",
	       assertions);
	return 0;
#endif
}

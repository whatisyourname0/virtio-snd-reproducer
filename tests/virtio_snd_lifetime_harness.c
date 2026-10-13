/* SPDX-License-Identifier: GPL-2.0-only */
/* Host shims for source-extracted virtio-snd PCM error paths. */
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef EBADFD
#define EBADFD 77
#endif

typedef uint8_t u8;
typedef uint32_t u32;
typedef unsigned int gfp_t;
typedef unsigned long snd_pcm_uframes_t;

#define GFP_KERNEL 0
#define GFP_ATOMIC 0
#define MSEC_PER_SEC 1000
#define SNDRV_PCM_STREAM_PLAYBACK 0
#define SNDRV_PCM_STREAM_CAPTURE 1
#define SNDRV_PCM_TRIGGER_START 1
#define SNDRV_PCM_TRIGGER_STOP 2
#define SNDRV_PCM_TRIGGER_PAUSE_RELEASE 3
#define SNDRV_PCM_TRIGGER_SUSPEND 4
#define SNDRV_PCM_TRIGGER_PAUSE_PUSH 5
#define VIRTIO_SND_PCM_F_MSG_POLLING 0
#define VIRTIO_SND_PCM_F_EVT_XRUNS 1
#define VIRTIO_SND_R_PCM_START 0x100
#define VIRTIO_SND_R_PCM_STOP 0x101
#define VIRTIO_SND_R_PCM_RELEASE 0x102
#define VIRTIO_SND_R_PCM_PREPARE 0x103
#define VIRTIO_SND_S_OK 0x8000U
#define VIRTIO_SND_S_IO_ERR 0x8001U
#define VIRTIO_SND_S_NOT_SUPP 0x8002U
#define PCM_MSG_SG_XFER 0
#define PCM_MSG_SG_STATUS 1
#define PCM_MSG_SG_DATA 2
#define SNDRV_PCM_HW_PARAM_PERIODS 1
#define SNDRV_PCM_FORMAT_S16_LE 2
#define fallthrough __attribute__((fallthrough))
#define min_t(type, left, right) ((type)(left) < (type)(right) ? \
				      (type)(left) : (type)(right))
#define guard(type) test_guard_##type
#ifdef TEST_STOPPED_LOCKING
#define scoped_guard(type, lock) \
	for (struct test_lock_scope __attribute__((cleanup(test_lock_scope_cleanup))) \
	     __test_scope = test_lock_scope_begin(lock); \
	     __test_scope.once; __test_scope.once = false)
#define wait_event_interruptible_timeout(wait, condition, timeout) \
	test_wait_event((wait), (condition), (timeout))
#define wait_event_timeout(wait, condition, timeout) \
	test_wait_event((wait), (condition), (timeout))
#else
#define scoped_guard(type, lock) \
	for (unsigned int __guard_once = ((void)(lock), 1U); \
	     __guard_once; __guard_once = 0)
#define wait_event_interruptible_timeout(wait, condition, timeout) \
	((void)(wait), (void)(timeout), (condition) ? 1L : 0L)
#define wait_event_timeout(wait, condition, timeout) \
	((void)(wait), (void)(timeout), (condition) ? 1L : 0L)
#endif
#define dev_err(device, ...) ((void)(device))
#define dev_warn(device, ...) ((void)(device))
#define dev_err_ratelimited(device, ...) do { \
	(void)(device); \
	ratelimited_errors++; \
} while (0)

struct device { unsigned int unused; };
struct virtio_device { struct device dev; };
struct virtio_snd;
struct virtio_pcm_substream;
struct virtio_pcm_msg;
struct virtio_snd_msg;
typedef int snd_pcm_format_t;
struct list_head { struct list_head *next, *prev; };
struct completion { unsigned int done; };
typedef unsigned int refcount_t;
struct virtio_pcm_hw { unsigned int unused; };
struct snd_pcm_hw_params {
	unsigned int buffer_bytes;
	unsigned int period_bytes;
	unsigned int channels;
	unsigned int rate;
	unsigned int periods;
	snd_pcm_format_t format;
};
struct snd_pcm_runtime {
	void *hw;
	unsigned int period_bytes;
	unsigned int rate;
	unsigned int channels;
	snd_pcm_format_t format;
	long delay;
	bool stop_operating;
};
struct snd_pcm_substream {
	struct snd_pcm_runtime *runtime;
	void *private_data;
	int stream;
	unsigned int number;
};
struct scatterlist { void *address; unsigned int length; };
struct virtio_snd_hdr { u32 code; };
struct virtio_snd_pcm_xfer { u32 stream_id; };
struct virtio_snd_pcm_status { u32 status; u32 latency_bytes; };
struct virtio_pcm_msg {
	struct virtio_pcm_substream *substream;
	struct virtio_snd_pcm_xfer xfer;
	struct virtio_snd_pcm_status status;
	size_t length;
	struct scatterlist sgs[3];
};

#define MAX_PENDING 8
#define MAX_COMMANDS 16
struct virtqueue {
	void *pending_messages[MAX_PENDING];
	unsigned int pending;
	unsigned int accepted;
	unsigned int add_calls;
	unsigned int fail_add_call;
	unsigned int kicks;
};
struct virtio_snd_queue { int lock; struct virtqueue *vqueue; };
struct virtio_snd {
	struct virtio_device *vdev;
	struct virtio_snd_queue tx_queue;
	struct virtio_snd_queue rx_queue;
	struct virtio_snd_queue ctl_queue;
	struct list_head ctl_msgs;
	bool ctl_msgs_blocked;
};
struct work_struct { unsigned int schedules; };
struct wait_queue_head { unsigned int wakes; };
typedef struct wait_queue_head wait_queue_head_t;
struct snd_pcm_indirect {
	unsigned int sw_buffer_size;
	unsigned int hw_buffer_size;
	unsigned long sw_data;
};
struct virtio_pcm_substream {
	struct virtio_snd *snd;
	struct snd_pcm_substream *substream;
	struct snd_pcm_indirect pcm_indirect;
	struct virtio_pcm_msg **msgs;
	struct virtio_pcm_hw *hw;
	struct work_struct elapsed_period;
	wait_queue_head_t msg_empty;
	int lock;
	u32 sid;
	u32 features;
	u32 direction;
	size_t buffer_bytes;
	size_t hw_ptr;
	unsigned int nmsgs;
	unsigned int msg_count;
	bool xfer_enabled;
	bool xfer_xrun;
	bool stopped;
	bool suspended;
};

#ifdef TEST_STOPPED_LOCKING
#define TEST_LOCK_STACK_SIZE 16
struct test_lock_scope {
	unsigned int base_depth;
	bool once;
};

static int *test_held_locks[TEST_LOCK_STACK_SIZE];
static unsigned int test_lock_depth;
static unsigned int test_lock_overflows;
static unsigned int test_lock_underflows;
static unsigned int stopped_accesses;
static unsigned int stopped_access_violations;
static unsigned int sleeping_with_lock;
static unsigned int pending_lock_reentries;

static void test_lock_push(int *lock)
{
	if (test_lock_depth == TEST_LOCK_STACK_SIZE) {
		test_lock_overflows++;
		return;
	}
	test_held_locks[test_lock_depth++] = lock;
}

static bool test_lock_held(int *lock)
{
	unsigned int i;

	for (i = 0; i < test_lock_depth; i++)
		if (test_held_locks[i] == lock)
			return true;
	return false;
}

static void test_lock_pop(void)
{
	if (!test_lock_depth) {
		test_lock_underflows++;
		return;
	}
	test_held_locks[--test_lock_depth] = NULL;
}

static struct test_lock_scope test_lock_scope_begin(int *lock)
{
	struct test_lock_scope scope = {
		.base_depth = test_lock_depth,
		.once = true,
	};

	test_lock_push(lock);
	return scope;
}

static void test_lock_scope_cleanup(struct test_lock_scope *scope)
{
	if (test_lock_depth < scope->base_depth)
		test_lock_underflows++;
	while (test_lock_depth > scope->base_depth)
		test_lock_pop();
}

static void test_guard_spinlock(int *lock)
{
	test_lock_push(lock);
}

static void test_guard_spinlock_irqsave(int *lock)
{
	test_lock_push(lock);
}

static bool *test_stopped_slot(struct virtio_pcm_substream *vss)
{
	stopped_accesses++;
	if (!test_lock_held(&vss->lock))
		stopped_access_violations++;
	return &vss->stopped;
}

static void test_sleep_site(void)
{
	if (test_lock_depth)
		sleeping_with_lock++;
}

static long test_wait_event(wait_queue_head_t wait, bool condition,
			    unsigned int timeout)
{
	(void)wait;
	(void)timeout;
	test_sleep_site();
	return condition ? 1L : 0L;
}

static void reset_lock_audit(void)
{
	memset(test_held_locks, 0, sizeof(test_held_locks));
	test_lock_depth = 0;
	test_lock_overflows = 0;
	test_lock_underflows = 0;
	stopped_accesses = 0;
	stopped_access_violations = 0;
	sleeping_with_lock = 0;
	pending_lock_reentries = 0;
}
#else
static void test_guard_spinlock(int *lock) { (void)lock; }
static void test_guard_spinlock_irqsave(int *lock) { (void)lock; }
static void test_sleep_site(void) { }
#endif

struct virtsnd_pcm_transfer {
	struct snd_pcm_indirect indirect;
	int error;
};
struct virtio_snd_msg {
	struct scatterlist sg_request;
	struct scatterlist sg_response;
	struct list_head list;
	struct completion notify;
	refcount_t ref_count;
	bool cancelled;
	unsigned int command;
};
struct virtio_pcm_stream {
	struct virtio_pcm_substream *substreams[1];
};
struct virtio_pcm { struct virtio_pcm_stream streams[2]; };

static unsigned int assertion_count;
static unsigned int failure_count;
static unsigned int sent_commands[MAX_COMMANDS];
static unsigned int sent_command_count;
static unsigned int prepare_commands;
static unsigned int alloc_attempts[MAX_COMMANDS];
static unsigned int fail_alloc_command = UINT32_MAX;
static unsigned int fail_send_command = UINT32_MAX;
static int fail_send_error = -EIO;
static struct virtio_pcm_substream *active_vss;
static unsigned int cancel_work_calls;
static unsigned int pcm_dev_set_params_calls;
static unsigned int pcm_msg_alloc_calls;
static unsigned int ctl_free_calls;
static unsigned int ratelimited_errors;
static long ctl_wait_result;
static int indirect_transfer_error;
static unsigned int indirect_transfer_calls;
static bool inject_ack_on_release;
static int release_ack_result;
static const char *active_test;

#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
#define list_entry(pointer, type, member) container_of(pointer, type, member)
#define list_first_entry(head, type, member) \
	list_entry((head)->next, type, member)
#define list_for_each_entry(position, head, member) \
	for (position = list_entry((head)->next, typeof(*position), member); \
	     &position->member != (head); \
	     position = list_entry(position->member.next, typeof(*position), member))
#define list_empty(head) ((head)->next == (head))
#define INIT_LIST_HEAD(head) do { (head)->next = (head); (head)->prev = (head); } while (0)
#define READ_ONCE(value) (value)
#define cpu_to_le32(value) ((u32)(value))
#define le32_to_cpu(value) ((u32)(value))

static void list_add_tail(struct list_head *node, struct list_head *head)
{
	node->prev = head->prev;
	node->next = head;
	head->prev->next = node;
	head->prev = node;
}

static void list_del(struct list_head *node)
{
	node->prev->next = node->next;
	node->next->prev = node->prev;
	node->next = node;
	node->prev = node;
}

static void complete(struct completion *completion)
{
	completion->done++;
}

static void *sg_virt(struct scatterlist *sg)
{
	return sg->address;
}

static void refcount_inc(refcount_t *ref_count)
{
	(*ref_count)++;
}

static bool refcount_dec_and_test(refcount_t *ref_count)
{
	return --(*ref_count) == 0;
}

static void kfree(void *pointer)
{
	ctl_free_calls++;
	free(pointer);
}

static struct virtio_snd_queue *virtsnd_control_queue(struct virtio_snd *snd)
{
	return &snd->ctl_queue;
}

static long wait_for_completion_interruptible_timeout(
	struct completion *completion, unsigned int timeout)
{
	(void)timeout;
	test_sleep_site();
	if (completion->done)
		return 1;
	return ctl_wait_result;
}

static void sg_init_one(struct scatterlist *sg, void *address,
			unsigned int length)
{
	sg->address = address;
	sg->length = length;
}

static struct virtio_snd_queue *virtsnd_pcm_queue(
	struct virtio_pcm_substream *vss)
{
	return vss->direction == SNDRV_PCM_STREAM_PLAYBACK ?
		&vss->snd->tx_queue : &vss->snd->rx_queue;
}

static unsigned int snd_pcm_lib_period_bytes(
	struct snd_pcm_substream *substream)
{
	return substream->runtime->period_bytes;
}

static unsigned long msecs_to_jiffies(unsigned int milliseconds)
{
	return milliseconds;
}

static unsigned int virtsnd_msg_timeout_ms = 1000;

static void cancel_work_sync(struct work_struct *work)
{
	(void)work;
	test_sleep_site();
	cancel_work_calls++;
}

static void schedule_work(struct work_struct *work)
{
	work->schedules++;
}

static void wake_up_all(wait_queue_head_t *wait)
{
	wait->wakes++;
}

#ifdef TEST_STOPPED_LOCKING
static void virtsnd_pcm_msg_complete(struct virtio_pcm_msg *msg,
				     size_t written_bytes)
{
	struct virtio_pcm_substream *vss = msg->substream;

	(void)written_bytes;
	test_lock_push(&vss->lock);
	msg->length = 0;
	if (vss->msg_count)
		vss->msg_count--;
	if (!vss->msg_count)
		wake_up_all(&vss->msg_empty);
	test_lock_pop();
}
#endif

static void *snd_pcm_substream_chip(struct snd_pcm_substream *substream)
{
	return substream->private_data;
}

static int snd_pcm_hw_constraint_integer(struct snd_pcm_runtime *runtime,
						 unsigned int parameter)
{
	(void)runtime;
	(void)parameter;
	return 0;
}

static unsigned int params_buffer_bytes(struct snd_pcm_hw_params *params)
{
	return params->buffer_bytes;
}

static unsigned int params_period_bytes(struct snd_pcm_hw_params *params)
{
	return params->period_bytes;
}

static unsigned int params_channels(struct snd_pcm_hw_params *params)
{
	return params->channels;
}

static snd_pcm_format_t params_format(struct snd_pcm_hw_params *params)
{
	return params->format;
}

static unsigned int params_rate(struct snd_pcm_hw_params *params)
{
	return params->rate;
}

static unsigned int params_periods(struct snd_pcm_hw_params *params)
{
	return params->periods;
}

static long bytes_to_frames(struct snd_pcm_runtime *runtime, size_t bytes)
{
	return runtime->period_bytes ? bytes / runtime->period_bytes : 0;
}

static unsigned int snd_pcm_lib_buffer_bytes(
	struct snd_pcm_substream *substream)
{
	(void)substream;
	return active_vss ? active_vss->buffer_bytes : 0;
}

typedef void (*snd_pcm_indirect_copy_t)(struct snd_pcm_substream *substream,
					 struct snd_pcm_indirect *rec,
					 size_t bytes);

static int snd_pcm_indirect_playback_transfer(
	struct snd_pcm_substream *substream, struct snd_pcm_indirect *rec,
	snd_pcm_indirect_copy_t copy)
{
	(void)substream;
	(void)rec;
	(void)copy;
	indirect_transfer_calls++;
	return indirect_transfer_error;
}

static int snd_pcm_indirect_capture_transfer(
	struct snd_pcm_substream *substream, struct snd_pcm_indirect *rec,
	snd_pcm_indirect_copy_t copy)
{
	(void)substream;
	(void)rec;
	(void)copy;
	indirect_transfer_calls++;
	return indirect_transfer_error;
}

static void virtsnd_pcm_msg_reset(struct virtio_pcm_substream *vss)
{
	unsigned int i;

	for (i = 0; vss->msgs && i < vss->nmsgs; i++)
		vss->msgs[i]->length = 0;
}

static int virtsnd_pcm_dev_set_params(struct virtio_pcm_substream *vss,
				      unsigned int buffer_bytes,
				      unsigned int period_bytes,
				      unsigned int channels,
				      snd_pcm_format_t format,
				      unsigned int rate)
{
	(void)vss;
	(void)buffer_bytes;
	(void)period_bytes;
	(void)channels;
	(void)format;
	(void)rate;
	test_sleep_site();
	pcm_dev_set_params_calls++;
	return 0;
}

static void virtsnd_pcm_msg_free(struct virtio_pcm_substream *vss)
{
	(void)vss;
}

static int virtsnd_pcm_msg_alloc(struct virtio_pcm_substream *vss,
				 unsigned int periods,
				 unsigned int period_bytes)
{
	(void)vss;
	(void)periods;
	(void)period_bytes;
	test_sleep_site();
	pcm_msg_alloc_calls++;
	return 0;
}

static int virtqueue_add_sgs(struct virtqueue *vqueue,
			     struct scatterlist **sgs, unsigned int out_num,
			     unsigned int in_num, void *data, gfp_t gfp)
{
	(void)sgs;
	(void)out_num;
	(void)in_num;
	(void)gfp;
	vqueue->add_calls++;
	if (vqueue->fail_add_call == vqueue->add_calls)
		return -EIO;
	if (vqueue->pending == MAX_PENDING)
		return -ENOSPC;
	vqueue->pending_messages[vqueue->pending++] = data;
	vqueue->accepted++;
	return 0;
}

static bool virtqueue_kick_prepare(struct virtqueue *vqueue)
{
	return vqueue->pending != 0;
}

static void virtqueue_notify(struct virtqueue *vqueue)
{
	vqueue->kicks++;
}

static void check(bool condition, const char *name, int line)
{
	assertion_count++;
	if (!condition) {
		fprintf(stderr, "FAIL [%s] %s at line %d\n", active_test,
			name, line);
		failure_count++;
	}
}

#define CHECK(condition) check((condition), #condition, __LINE__)

static void virtsnd_pcm_msg_complete(struct virtio_pcm_msg *msg,
				     size_t written_bytes);

static void drain_pcm_queue(struct virtio_pcm_substream *vss)
{
	struct virtqueue *vqueue = virtsnd_pcm_queue(vss)->vqueue;

	while (vqueue->pending) {
		struct virtio_pcm_msg *msg =
			vqueue->pending_messages[0];
		unsigned int i;

		for (i = 1; i < vqueue->pending; i++)
			vqueue->pending_messages[i - 1] =
				vqueue->pending_messages[i];
		vqueue->pending--;
		virtsnd_pcm_msg_complete(msg, sizeof(msg->status) + msg->length);
	}
}

static struct virtio_snd_msg *virtsnd_pcm_ctl_msg_alloc(
	struct virtio_pcm_substream *vss, unsigned int command, gfp_t gfp)
{
	struct virtio_snd_msg *msg;

	(void)vss;
	(void)gfp;
	test_sleep_site();
	alloc_attempts[command % MAX_COMMANDS]++;
	if (command == fail_alloc_command)
		return NULL;
	msg = calloc(1, sizeof(*msg));
	if (msg)
		msg->command = command;
	return msg;
}

#ifdef TEST_STOPPED_LOCKING
static int virtsnd_pcm_pb_ack(struct snd_pcm_substream *substream);
static int virtsnd_pcm_cp_ack(struct snd_pcm_substream *substream);
#endif

static int virtsnd_ctl_msg_send_sync(struct virtio_snd *snd,
				     struct virtio_snd_msg *msg)
{
	unsigned int command = msg->command;
	int rc = 0;

	(void)snd;
	test_sleep_site();
	if (sent_command_count < MAX_COMMANDS)
		sent_commands[sent_command_count++] = command;
	if (command == VIRTIO_SND_R_PCM_PREPARE)
		prepare_commands++;
	if (command == fail_send_command)
		rc = fail_send_error;
	if (command == VIRTIO_SND_R_PCM_RELEASE && active_vss) {
#ifdef TEST_STOPPED_LOCKING
		if (inject_ack_on_release) {
			release_ack_result =
				active_vss->direction == SNDRV_PCM_STREAM_PLAYBACK ?
				virtsnd_pcm_pb_ack(active_vss->substream) :
				virtsnd_pcm_cp_ack(active_vss->substream);
			inject_ack_on_release = false;
		}
#endif
		drain_pcm_queue(active_vss);
	}
	free(msg);
	return rc;
}

int virtsnd_pcm_msg_send(struct virtio_pcm_substream *vss,
			 unsigned long offset, unsigned long bytes);
unsigned int virtsnd_pcm_msg_pending_num(struct virtio_pcm_substream *vss);
static int virtsnd_pcm_sync_stop(struct snd_pcm_substream *substream);
#ifdef TEST_STOPPED_LOCKING
static int virtsnd_pcm_pb_ack(struct snd_pcm_substream *substream);
static int virtsnd_pcm_cp_ack(struct snd_pcm_substream *substream);

unsigned int virtsnd_pcm_msg_pending_num(struct virtio_pcm_substream *vss)
{
	unsigned int count;

	if (test_lock_held(&vss->lock)) {
		pending_lock_reentries++;
		return vss->msg_count;
	}
	test_lock_push(&vss->lock);
	count = vss->msg_count;
	test_lock_pop();
	return count;
}
#endif

static void *virtsnd_ctl_msg_request(struct virtio_snd_msg *msg)
{
	return sg_virt(&msg->sg_request);
}

static void *virtsnd_ctl_msg_response(struct virtio_snd_msg *msg)
{
	return sg_virt(&msg->sg_response);
}

int virtsnd_ctl_msg_send(struct virtio_snd *snd,
			 struct virtio_snd_msg *msg,
			 struct scatterlist *out_sgs,
			 struct scatterlist *in_sgs, bool nowait);

/* EXTRACTED_VIRTSND_FUNCTIONS */

struct fixture {
	struct virtio_device vdev;
	struct virtio_snd snd;
	struct virtqueue tx_vq;
	struct virtqueue rx_vq;
	struct virtqueue ctl_vq;
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct virtio_pcm_msg messages[2];
	struct virtio_pcm_msg *message_ptrs[2];
	struct virtio_pcm pcm;
	struct virtio_pcm_hw pcm_hw;
	struct snd_pcm_hw_params hw_params;
};

static void fixture_init(struct fixture *fixture)
{
	memset(fixture, 0, sizeof(*fixture));
	fixture->snd.vdev = &fixture->vdev;
	fixture->snd.tx_queue.vqueue = &fixture->tx_vq;
	fixture->snd.rx_queue.vqueue = &fixture->rx_vq;
	fixture->snd.ctl_queue.vqueue = &fixture->ctl_vq;
	INIT_LIST_HEAD(&fixture->snd.ctl_msgs);
	fixture->vss.snd = &fixture->snd;
	fixture->vss.substream = &fixture->substream;
	fixture->vss.hw = &fixture->pcm_hw;
	fixture->vss.msgs = fixture->message_ptrs;
	fixture->vss.nmsgs = 2;
	fixture->vss.direction = SNDRV_PCM_STREAM_CAPTURE;
	fixture->vss.sid = 4;
	fixture->vss.buffer_bytes = 128;
	fixture->substream.runtime = &fixture->runtime;
	fixture->substream.private_data = &fixture->vss;
	fixture->substream.stream = SNDRV_PCM_STREAM_CAPTURE;
	fixture->substream.number = 0;
	fixture->runtime.period_bytes = 64;
	fixture->runtime.channels = 1;
	fixture->runtime.rate = 48000;
	fixture->runtime.format = SNDRV_PCM_FORMAT_S16_LE;
	fixture->pcm.streams[SNDRV_PCM_STREAM_CAPTURE].substreams[0] =
		&fixture->vss;
	fixture->hw_params.buffer_bytes = 128;
	fixture->hw_params.period_bytes = 64;
	fixture->hw_params.channels = 1;
	fixture->hw_params.rate = 48000;
	fixture->hw_params.periods = 2;
	fixture->hw_params.format = SNDRV_PCM_FORMAT_S16_LE;
	fixture->message_ptrs[0] = &fixture->messages[0];
	fixture->message_ptrs[1] = &fixture->messages[1];
	fixture->messages[0].substream = &fixture->vss;
	fixture->messages[1].substream = &fixture->vss;
	active_vss = &fixture->vss;
}

static void reset_faults(void)
{
	memset(sent_commands, 0, sizeof(sent_commands));
	memset(alloc_attempts, 0, sizeof(alloc_attempts));
	sent_command_count = 0;
	prepare_commands = 0;
	fail_alloc_command = UINT32_MAX;
	fail_send_command = UINT32_MAX;
	fail_send_error = -EIO;
	cancel_work_calls = 0;
	pcm_dev_set_params_calls = 0;
	pcm_msg_alloc_calls = 0;
	ctl_free_calls = 0;
	ratelimited_errors = 0;
	ctl_wait_result = 0;
	indirect_transfer_error = 0;
	indirect_transfer_calls = 0;
	inject_ack_on_release = false;
	release_ack_result = 0;
}

static void test_capture_completion_clamps_reported_length(void)
{
	struct fixture fixture;

	active_test = "capture-length-clamp";
	fixture_init(&fixture);
	fixture.vss.msg_count = 1;
	fixture.messages[0].length = 64;
	virtsnd_pcm_msg_complete(&fixture.messages[0],
				 sizeof(fixture.messages[0].status) + 80);
	CHECK(fixture.vss.hw_ptr == 64);
	CHECK(fixture.messages[0].length == 0);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.vss.msg_empty.wakes == 1);
	CHECK(ratelimited_errors == 1);
}

static void test_partial_queue_add_failure_kicks_prefix_and_recovers(void)
{
	struct fixture fixture;
	struct virtqueue *vqueue;
	int rc;

	active_test = "partial-send";
	fixture_init(&fixture);
	vqueue = fixture.snd.rx_queue.vqueue;
	vqueue->fail_add_call = 2;
	rc = virtsnd_pcm_msg_send(&fixture.vss, 0, fixture.vss.buffer_bytes);
	CHECK(rc == -EIO);
	CHECK(vqueue->accepted == 1);
	CHECK(vqueue->pending == 1);
	CHECK(vqueue->kicks == 1);
	CHECK(fixture.vss.msg_count == 1);
	CHECK(fixture.messages[1].length == 0);

	drain_pcm_queue(&fixture.vss);
	vqueue->fail_add_call = 0;
	rc = virtsnd_pcm_msg_send(&fixture.vss, 0, fixture.vss.buffer_bytes);
	CHECK(rc == 0);
	CHECK(vqueue->pending == 2);
	CHECK(fixture.vss.msg_count == 2);
	CHECK(fixture.messages[0].length == 64);
	CHECK(fixture.messages[1].length == 64);
	drain_pcm_queue(&fixture.vss);
}

static void test_first_queue_add_failure_leaves_no_partial_message(void)
{
	struct fixture fixture;
	struct virtqueue *vqueue;
	int rc;

	active_test = "first-add";
	fixture_init(&fixture);
	vqueue = fixture.snd.rx_queue.vqueue;
	vqueue->fail_add_call = 1;
	rc = virtsnd_pcm_msg_send(&fixture.vss, 0, fixture.vss.buffer_bytes);
	CHECK(rc == -EIO);
	CHECK(vqueue->accepted == 0);
	CHECK(vqueue->pending == 0);
	CHECK(vqueue->kicks == 0);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.messages[0].length == 0);
}

static void test_polling_partial_failure_skips_notification(void)
{
	struct fixture fixture;
	struct virtqueue *vqueue;
	int rc;

	active_test = "polling-partial-send";
	fixture_init(&fixture);
	fixture.vss.features = 1U << VIRTIO_SND_PCM_F_MSG_POLLING;
	vqueue = fixture.snd.rx_queue.vqueue;
	vqueue->fail_add_call = 2;
	rc = virtsnd_pcm_msg_send(&fixture.vss, 0, fixture.vss.buffer_bytes);
	CHECK(rc == -EIO);
	CHECK(vqueue->accepted == 1);
	CHECK(vqueue->pending == 1);
	CHECK(vqueue->kicks == 0);
	CHECK(fixture.vss.msg_count == 1);
	CHECK(fixture.messages[1].length == 0);
	drain_pcm_queue(&fixture.vss);
	CHECK(fixture.vss.msg_count == 0);
}

static void test_start_allocation_failure_does_not_strand_capture_io(void)
{
	struct fixture fixture;
	int rc;

	active_test = "start-allocation";
	fixture_init(&fixture);
	fail_alloc_command = VIRTIO_SND_R_PCM_START;
	rc = virtsnd_pcm_trigger(&fixture.substream, SNDRV_PCM_TRIGGER_START);
	CHECK(rc == -ENOMEM);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(fixture.runtime.stop_operating);
	{
		unsigned int queued = fixture.snd.rx_queue.vqueue->pending;
		unsigned int attempts = alloc_attempts[VIRTIO_SND_R_PCM_START %
						      MAX_COMMANDS];

		rc = virtsnd_pcm_trigger(&fixture.substream,
					 SNDRV_PCM_TRIGGER_START);
		CHECK(rc == -EBADFD);
		CHECK(fixture.snd.rx_queue.vqueue->pending == queued);
		CHECK(fixture.snd.rx_queue.vqueue->add_calls == 2);
		CHECK(alloc_attempts[VIRTIO_SND_R_PCM_START % MAX_COMMANDS] ==
		      attempts);
	}
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 0);
	drain_pcm_queue(&fixture.vss);
	CHECK(fixture.vss.elapsed_period.schedules == 0);
}

static void test_start_send_failure_disables_transfer_before_drain(void)
{
	struct fixture fixture;
	int rc;

	active_test = "start-send";
	fixture_init(&fixture);
	fail_send_command = VIRTIO_SND_R_PCM_START;
	rc = virtsnd_pcm_trigger(&fixture.substream, SNDRV_PCM_TRIGGER_START);
	CHECK(rc == -EIO);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(fixture.runtime.stop_operating);
	fail_send_command = UINT32_MAX;
	rc = virtsnd_pcm_trigger(&fixture.substream,
				 SNDRV_PCM_TRIGGER_START);
	CHECK(rc == -EBADFD);
	CHECK(sent_command_count == 1);
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 0);
	drain_pcm_queue(&fixture.vss);
	CHECK(fixture.vss.elapsed_period.schedules == 0);
}

static void test_prepare_waits_for_release_retry_after_error(void)
{
	struct fixture fixture;
	int rc;

	active_test = "prepare-after-release-error";
	fixture_init(&fixture);
	fixture.vss.stopped = true;
	fixture.runtime.stop_operating = true;
	fixture.substream.private_data = &fixture.pcm;
	fail_send_command = VIRTIO_SND_R_PCM_RELEASE;
	rc = virtsnd_pcm_open(&fixture.substream);
	CHECK(rc == -EIO);
	CHECK(fixture.vss.stopped);
	CHECK(fixture.runtime.stop_operating);
	rc = virtsnd_pcm_prepare(&fixture.substream);
	CHECK(rc == -EBADFD);
	CHECK(prepare_commands == 0);
	rc = virtsnd_pcm_hw_params(&fixture.substream, &fixture.hw_params);
	CHECK(rc == -EBADFD);
	CHECK(pcm_dev_set_params_calls == 0);

	fail_send_command = UINT32_MAX;
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);
	fixture.runtime.stop_operating = false;
	CHECK(virtsnd_pcm_hw_params(&fixture.substream, &fixture.hw_params) == 0);
	CHECK(pcm_dev_set_params_calls == 1);
	CHECK(virtsnd_pcm_prepare(&fixture.substream) == 0);
	CHECK(prepare_commands == 1);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_START) == 0);
	CHECK(fixture.vss.xfer_enabled);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 2);
	CHECK(sent_commands[sent_command_count - 1] == VIRTIO_SND_R_PCM_START);
	drain_pcm_queue(&fixture.vss);
}

static void test_normal_capture_pause_and_release_sequence(void)
{
	struct fixture fixture;
	const unsigned int expected[] = {
		VIRTIO_SND_R_PCM_PREPARE,
		VIRTIO_SND_R_PCM_START,
		VIRTIO_SND_R_PCM_STOP,
		VIRTIO_SND_R_PCM_START,
		VIRTIO_SND_R_PCM_STOP,
		VIRTIO_SND_R_PCM_RELEASE,
	};
	unsigned int i;

	active_test = "normal-pause-release";
	fixture_init(&fixture);
	CHECK(virtsnd_pcm_prepare(&fixture.substream) == 0);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_START) == 0);
	CHECK(fixture.vss.xfer_enabled);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 2);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_PAUSE_PUSH) == 0);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(!fixture.vss.stopped);
	drain_pcm_queue(&fixture.vss);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_PAUSE_RELEASE) == 0);
	CHECK(fixture.vss.xfer_enabled);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 2);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_STOP) == 0);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(fixture.vss.stopped);
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 0);
	CHECK(sent_command_count == sizeof(expected) / sizeof(expected[0]));
	for (i = 0; i < sizeof(expected) / sizeof(expected[0]); i++)
		CHECK(sent_commands[i] == expected[i]);
}

static void test_idle_sync_stop_quiesces_work_without_release(void)
{
	struct fixture fixture;

	active_test = "sync-stop-idle";
	fixture_init(&fixture);
	fixture.vss.xfer_enabled = true;
	fixture.vss.elapsed_period.schedules = 1;
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(cancel_work_calls == 1);
	CHECK(sent_command_count == 0);
	CHECK(fixture.vss.msg_count == 0);
}

#ifdef TEST_STOPPED_LOCKING
static void seed_pending_capture_message(struct fixture *fixture)
{
	struct virtqueue *vqueue = fixture->snd.rx_queue.vqueue;

	fixture->vss.msg_count = 1;
	fixture->messages[0].length = 64;
	vqueue->pending_messages[0] = &fixture->messages[0];
	vqueue->pending = 1;
	vqueue->accepted = 1;
}

static void test_ack_failure_blocks_lifecycle_until_release(int direction)
{
	struct fixture fixture;
	int rc;

	active_test = direction == SNDRV_PCM_STREAM_PLAYBACK ?
		"playback-ack-error-lifecycle-release" :
		"capture-ack-error-lifecycle-release";
	fixture_init(&fixture);
	fixture.vss.direction = direction;
	fixture.substream.stream = direction;
	fixture.vss.xfer_enabled = true;
	indirect_transfer_error = -EIO;

	rc = direction == SNDRV_PCM_STREAM_PLAYBACK ?
		virtsnd_pcm_pb_ack(&fixture.substream) :
		virtsnd_pcm_cp_ack(&fixture.substream);
	CHECK(rc == -EPIPE);
	CHECK(fixture.vss.stopped);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(fixture.runtime.stop_operating);
	CHECK(indirect_transfer_calls == 1);

	CHECK(virtsnd_pcm_hw_params(&fixture.substream,
				    &fixture.hw_params) == -EBADFD);
	CHECK(virtsnd_pcm_prepare(&fixture.substream) == -EBADFD);
	CHECK(pcm_dev_set_params_calls == 0);
	CHECK(prepare_commands == 0);

	indirect_transfer_error = 0;
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);
	CHECK(sent_command_count == 1);
	CHECK(sent_commands[0] == VIRTIO_SND_R_PCM_RELEASE);

	fixture.runtime.stop_operating = false;
	CHECK(virtsnd_pcm_hw_params(&fixture.substream,
				    &fixture.hw_params) == 0);
	CHECK(virtsnd_pcm_prepare(&fixture.substream) == 0);
	CHECK(prepare_commands == 1);
}

static void test_ack_during_release_is_rejected_and_release_clears_state(void)
{
	struct fixture fixture;

	active_test = "ack-during-release";
	fixture_init(&fixture);
	seed_pending_capture_message(&fixture);
	inject_ack_on_release = true;

	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(release_ack_result == -EPIPE);
	CHECK(indirect_transfer_calls == 0);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.snd.rx_queue.vqueue->pending == 0);
	CHECK(!fixture.vss.stopped);
	CHECK(sent_command_count == 1);
	CHECK(sent_commands[0] == VIRTIO_SND_R_PCM_RELEASE);
}

static void test_open_detects_pending_io_and_retries_failed_release(void)
{
	struct fixture fixture;

	active_test = "open-pending-release-retry";
	fixture_init(&fixture);
	fixture.substream.private_data = &fixture.pcm;
	seed_pending_capture_message(&fixture);
	fail_send_command = VIRTIO_SND_R_PCM_RELEASE;

	CHECK(virtsnd_pcm_open(&fixture.substream) == -EIO);
	CHECK(fixture.vss.stopped);
	CHECK(pending_lock_reentries == 0);
	CHECK(fixture.vss.msg_count == 0);

	fail_send_command = UINT32_MAX;
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);
	CHECK(virtsnd_pcm_hw_params(&fixture.substream,
				    &fixture.hw_params) == 0);
	CHECK(virtsnd_pcm_prepare(&fixture.substream) == 0);
}

static void test_stop_and_suspend_publish_state_under_lock(void)
{
	struct fixture fixture;

	active_test = "stop-suspend-locking";
	fixture_init(&fixture);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_STOP) == 0);
	CHECK(fixture.vss.stopped);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);

	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_SUSPEND) == 0);
	CHECK(fixture.vss.stopped);
	CHECK(fixture.vss.suspended);
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);
}

static void test_failed_release_keeps_stopped_until_retry(void)
{
	struct fixture fixture;

	active_test = "release-error-keeps-stopped";
	fixture_init(&fixture);
	fixture.vss.stopped = true;
	fail_send_command = VIRTIO_SND_R_PCM_RELEASE;

	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == -EIO);
	CHECK(fixture.vss.stopped);
	CHECK(virtsnd_pcm_hw_params(&fixture.substream,
				    &fixture.hw_params) == -EBADFD);
	CHECK(virtsnd_pcm_prepare(&fixture.substream) == -EBADFD);
	CHECK(virtsnd_pcm_trigger(&fixture.substream,
				  SNDRV_PCM_TRIGGER_START) == -EBADFD);
	CHECK(sent_command_count == 1);

	fail_send_command = UINT32_MAX;
	CHECK(virtsnd_pcm_sync_stop(&fixture.substream) == 0);
	CHECK(!fixture.vss.stopped);
}

static void test_stopped_lock_contract(void)
{
	active_test = "stopped-lock-audit";
	CHECK(stopped_accesses > 0);
	CHECK(stopped_access_violations == 0);
	CHECK(sleeping_with_lock == 0);
	CHECK(pending_lock_reentries == 0);
	CHECK(test_lock_depth == 0);
	CHECK(test_lock_overflows == 0);
	CHECK(test_lock_underflows == 0);
}

int main(void)
{
	reset_faults();
	reset_lock_audit();
	test_ack_failure_blocks_lifecycle_until_release(
		SNDRV_PCM_STREAM_PLAYBACK);
	reset_faults();
	test_ack_failure_blocks_lifecycle_until_release(
		SNDRV_PCM_STREAM_CAPTURE);
	reset_faults();
	test_ack_during_release_is_rejected_and_release_clears_state();
	reset_faults();
	test_open_detects_pending_io_and_retries_failed_release();
	reset_faults();
	test_stop_and_suspend_publish_state_under_lock();
	reset_faults();
	test_failed_release_keeps_stopped_until_retry();
	test_stopped_lock_contract();
	if (failure_count) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertion_count, failure_count);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertion_count);
	return 0;
}
#else
int main(void)
{
	reset_faults();
	test_capture_completion_clamps_reported_length();
	reset_faults();
	test_partial_queue_add_failure_kicks_prefix_and_recovers();
	reset_faults();
	test_first_queue_add_failure_leaves_no_partial_message();
	reset_faults();
	test_polling_partial_failure_skips_notification();
	reset_faults();
	test_start_allocation_failure_does_not_strand_capture_io();
	reset_faults();
	test_start_send_failure_disables_transfer_before_drain();
	reset_faults();
	test_prepare_waits_for_release_retry_after_error();
	reset_faults();
	test_normal_capture_pause_and_release_sequence();
	reset_faults();
	test_idle_sync_stop_quiesces_work_without_release();
	if (failure_count) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertion_count, failure_count);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertion_count);
	return 0;
}
#endif

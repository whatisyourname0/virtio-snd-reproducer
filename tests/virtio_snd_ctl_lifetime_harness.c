/* SPDX-License-Identifier: GPL-2.0-only */
/* Host shims for source-extracted virtio-snd control-message lifetime paths. */
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef unsigned int gfp_t;
typedef unsigned int refcount_t;

#define GFP_ATOMIC 0
#define VIRTIO_SND_S_OK 0x8000U
#define VIRTIO_SND_S_NOT_SUPP 0x8001U
#define VIRTIO_SND_S_IO_ERR 0x8002U
#define cpu_to_le32(value) ((u32)(value))
#define le32_to_cpu(value) ((u32)(value))
#define READ_ONCE(value) (value)
#define WRITE_ONCE(value, new_value) ((value) = (new_value))
#define guard(type) test_guard_##type
#define scoped_guard(type, lock) \
	for (unsigned int __guard_once = ((void)(lock), 1U); \
	     __guard_once; __guard_once = 0)
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

struct list_head { struct list_head *next, *prev; };
struct completion { unsigned int done; };
struct scatterlist { void *address; unsigned int length; };
struct device { unsigned int unused; };
struct virtio_device { struct device dev; };
struct virtio_snd_hdr { u32 code; };
struct virtio_snd_msg {
	struct scatterlist sg_request;
	struct scatterlist sg_request_data;
	struct scatterlist sg_response;
	struct scatterlist sg_response_data;
	size_t request_data_size;
	size_t response_data_size;
	struct list_head list;
	struct completion notify;
	refcount_t ref_count;
	bool cancelled;
	union {
		struct virtio_snd_hdr align;
		unsigned char bytes[2 * sizeof(struct virtio_snd_hdr)];
	} data;
};

#define MAX_PENDING 8
struct virtqueue {
	void *pending_messages[MAX_PENDING];
	unsigned int pending;
	unsigned int add_calls;
	unsigned int kicks;
};
struct virtio_snd_queue { int lock; struct virtqueue *vqueue; };
struct virtio_snd {
	struct virtio_device *vdev;
	struct virtio_snd_queue ctl_queue;
	struct list_head ctl_msgs;
	bool ctl_msgs_blocked;
};

static unsigned int completed_count;
static unsigned int freed_count;
static long wait_result;
static bool cancel_during_wait;
static bool cleanup_during_wait;
static struct virtio_snd *wait_snd;
static const char *active_test;
static unsigned int assertions;
static unsigned int failures;

void virtsnd_ctl_msg_cancel_all(struct virtio_snd *snd);
void virtsnd_ctl_msg_complete(struct virtio_snd_msg *msg);
void virtsnd_ctl_msg_cleanup(struct virtio_snd *snd);

static void test_guard_spinlock_irqsave(int *lock) { (void)lock; }

static void dev_err(const struct device *device, const char *format, ...)
{
	(void)device;
	(void)format;
}

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

static bool list_is_linked(const struct list_head *node)
{
	return node->next != node;
}

static void complete(struct completion *completion)
{
	completion->done++;
	completed_count++;
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
	freed_count++;
	free(pointer);
}

static struct virtio_snd_queue *virtsnd_control_queue(struct virtio_snd *snd)
{
	return &snd->ctl_queue;
}

static unsigned int virtsnd_msg_timeout_ms = 1000;

static unsigned int msecs_to_jiffies(unsigned int milliseconds)
{
	return milliseconds;
}

static long wait_for_completion_interruptible_timeout(
	struct completion *completion, unsigned int timeout)
{
	(void)timeout;
	if (cancel_during_wait && !completion->done) {
		cancel_during_wait = false;
		virtsnd_ctl_msg_cancel_all(wait_snd);
		if (cleanup_during_wait) {
			cleanup_during_wait = false;
			/* Reset/del_vqs makes outstanding callbacks impossible. */
			wait_snd->ctl_queue.vqueue->pending = 0;
			/* The pre-patch baseline has no post-reset cleanup entry point. */
#ifdef VIRTSND_CTL_TEARDOWN_AVAILABLE
			virtsnd_ctl_msg_cleanup(wait_snd);
#else
			abort();
#endif
		}
	}
	if (completion->done)
		return 1;
	return wait_result;
}

static void list_init(struct list_head *head)
{
	head->next = head;
	head->prev = head;
}

static void sg_init_one(struct scatterlist *sg, void *address,
			unsigned int length)
{
	sg->address = address;
	sg->length = length;
}

static bool virtqueue_kick_prepare(struct virtqueue *vqueue)
{
	return vqueue->pending != 0;
}

static void virtqueue_notify(struct virtqueue *vqueue)
{
	vqueue->kicks++;
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
	if (vqueue->pending == MAX_PENDING)
		return -ENOSPC;
	vqueue->pending_messages[vqueue->pending++] = data;
	return 0;
}

static void *virtsnd_ctl_msg_request(struct virtio_snd_msg *msg)
{
	return sg_virt(&msg->sg_request);
}

static void *virtsnd_ctl_msg_response(struct virtio_snd_msg *msg)
{
	return sg_virt(&msg->sg_response);
}

static void check(bool condition, const char *expression, int line)
{
	assertions++;
	if (!condition) {
		fprintf(stderr, "FAIL [%s] %s at line %d\n", active_test,
			expression, line);
		failures++;
	}
}

#define CHECK(condition) check((condition), #condition, __LINE__)

#ifdef VIRTSND_CTL_NEW_SEND_API
#define test_ctl_msg_send(snd, msg, nowait) \
	virtsnd_ctl_msg_send((snd), (msg), (nowait))
#else
#define test_ctl_msg_send(snd, msg, nowait) \
	virtsnd_ctl_msg_send((snd), (msg), NULL, NULL, (nowait))
#endif

/* EXTRACTED_VIRTSND_CTL_FUNCTIONS */

struct fixture {
	struct virtio_device vdev;
	struct virtio_snd snd;
	struct virtqueue vqueue;
};

static void fixture_init(struct fixture *fixture)
{
	memset(fixture, 0, sizeof(*fixture));
	fixture->snd.vdev = &fixture->vdev;
	fixture->snd.ctl_queue.vqueue = &fixture->vqueue;
	list_init(&fixture->snd.ctl_msgs);
	completed_count = 0;
	freed_count = 0;
	wait_result = 0;
	cancel_during_wait = false;
	cleanup_during_wait = false;
	wait_snd = NULL;
}

static struct virtio_snd_msg *new_message(unsigned int command)
{
	struct virtio_snd_msg *msg = calloc(
	1, sizeof(*msg));
	struct virtio_snd_hdr *request;

	if (!msg)
		return NULL;
	list_init(&msg->list);
	msg->ref_count = 1;
	sg_init_one(&msg->sg_request, msg->data.bytes, sizeof(*request));
	sg_init_one(&msg->sg_response, msg->data.bytes + sizeof(*request),
		    sizeof(*request));
	request = virtsnd_ctl_msg_request(msg);
	request->code = command;
	return msg;
}

static unsigned int pending_list_length(struct virtio_snd *snd)
{
	struct list_head *node;
	unsigned int count = 0;

	for (node = snd->ctl_msgs.next; node != &snd->ctl_msgs;
	     node = node->next)
		count++;
	return count;
}

static struct virtio_snd_msg *take_queued_message(struct virtqueue *vqueue)
{
	struct virtio_snd_msg *msg;
	unsigned int i;

	if (!vqueue->pending)
		return NULL;
	msg = vqueue->pending_messages[0];
	for (i = 1; i < vqueue->pending; i++)
		vqueue->pending_messages[i - 1] = vqueue->pending_messages[i];
	vqueue->pending--;
	return msg;
}

static void test_timeout_cancel_late_completion(void)
{
	struct fixture fixture;
	struct virtio_snd_msg *msg;
	int rc;

	active_test = "control-timeout-cancel-late-complete";
	fixture_init(&fixture);
	msg = new_message(0x1234);
	CHECK(msg != NULL);
	rc = test_ctl_msg_send(&fixture.snd, msg, false);
	CHECK(rc == -ETIMEDOUT);
#ifdef VIRTSND_CTL_TEARDOWN_AVAILABLE
	CHECK(msg->ref_count == 1);
	CHECK(list_is_linked(&msg->list));
	CHECK(fixture.vqueue.pending == 1);
#endif
	virtsnd_ctl_msg_cancel_all(&fixture.snd);
#ifdef VIRTSND_CTL_TEARDOWN_AVAILABLE
	CHECK(fixture.snd.ctl_msgs_blocked);
	CHECK(pending_list_length(&fixture.snd) == 1);
	CHECK(msg->cancelled);
	CHECK(msg->ref_count == 1);
	CHECK(msg->notify.done == 0);
	CHECK(take_queued_message(&fixture.vqueue) == msg);
	virtsnd_ctl_msg_complete(msg);
	CHECK(pending_list_length(&fixture.snd) == 0);
	CHECK(completed_count == 1);
	CHECK(freed_count == 1);
#else
	/* The legacy cancel path drops queue ownership before a late callback. */
	virtsnd_ctl_msg_complete(msg);
#endif
}

#ifdef VIRTSND_CTL_TEARDOWN_AVAILABLE
static void test_reset_cleanup_wakes_sync_sender(void)
{
	struct fixture fixture;
	struct virtio_snd_msg *msg;
	int rc;

	active_test = "control-reset-cleanup-wakes-sender";
	fixture_init(&fixture);
	msg = new_message(0x2234);
	CHECK(msg != NULL);
	wait_snd = &fixture.snd;
	cancel_during_wait = true;
	cleanup_during_wait = true;
	rc = test_ctl_msg_send(&fixture.snd, msg, false);
	CHECK(rc == -ENODEV);
	CHECK(fixture.snd.ctl_msgs_blocked);
	CHECK(pending_list_length(&fixture.snd) == 0);
	CHECK(completed_count == 1);
	CHECK(freed_count == 1);
}

static void test_blocked_enqueue_quiesce_cleanup_and_restore_enable(void)
{
	struct fixture fixture;
	struct virtio_snd_msg *pending;
	struct virtio_snd_msg *after_restore;
	unsigned int add_calls;
	unsigned int frees_before;
	int rc;

	active_test = "control-quiesce-cleanup-restore";
	fixture_init(&fixture);
	pending = new_message(0x3234);
	CHECK(pending != NULL);
	CHECK(test_ctl_msg_send(&fixture.snd, pending, true) == 0);
	CHECK(pending_list_length(&fixture.snd) == 1);
	CHECK(pending->ref_count == 1);
	virtsnd_ctl_msg_cancel_all(&fixture.snd);
	CHECK(fixture.snd.ctl_msgs_blocked);
	CHECK(pending->notify.done == 0);
	CHECK(pending->ref_count == 1);
	add_calls = fixture.vqueue.add_calls;
	after_restore = new_message(0x4234);
	CHECK(after_restore != NULL);
	rc = test_ctl_msg_send(&fixture.snd, after_restore, true);
	CHECK(rc == -ENODEV);
	CHECK(fixture.vqueue.add_calls == add_calls);
	CHECK(fixture.vqueue.pending == 1);
	CHECK(pending_list_length(&fixture.snd) == 1);

	/* Model the completed reset + del_vqs boundary before orphan cleanup. */
	fixture.vqueue.pending = 0;
	frees_before = freed_count;
	{
		unsigned int completes_before = completed_count;

		virtsnd_ctl_msg_cleanup(&fixture.snd);
		CHECK(completed_count == completes_before + 1);
	}
	CHECK(pending_list_length(&fixture.snd) == 0);
	CHECK(freed_count == frees_before + 1);

	virtsnd_ctl_msg_enable(&fixture.snd);
	CHECK(!fixture.snd.ctl_msgs_blocked);
	after_restore = new_message(0x5234);
	CHECK(after_restore != NULL);
	CHECK(test_ctl_msg_send(&fixture.snd, after_restore, true) == 0);
	CHECK(fixture.vqueue.add_calls == add_calls + 1);
	CHECK(fixture.vqueue.pending == 1);
	CHECK(pending_list_length(&fixture.snd) == 1);
	CHECK(take_queued_message(&fixture.vqueue) == after_restore);
	virtsnd_ctl_msg_complete(after_restore);
	CHECK(pending_list_length(&fixture.snd) == 0);
	CHECK(freed_count == frees_before + 2);
}
#endif

int main(void)
{
	test_timeout_cancel_late_completion();
#ifdef VIRTSND_CTL_TEARDOWN_AVAILABLE
	test_reset_cleanup_wakes_sync_sender();
	test_blocked_enqueue_quiesce_cleanup_and_restore_enable();
#endif
	if (failures) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertions, failures);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertions);
	return 0;
}

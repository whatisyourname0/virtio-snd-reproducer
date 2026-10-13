/* SPDX-License-Identifier: GPL-2.0-only */
/* Host shims for source-extracted virtio-snd control message paths. */
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* TEST_DEFINES */

typedef uint8_t u8;
typedef uint32_t u32;
typedef unsigned int gfp_t;
typedef int spinlock_t;
typedef unsigned int refcount_t;

#define GFP_ATOMIC 0
#define GFP_KERNEL 0
#define ERESTARTSYS 512
#define SNDRV_CTL_TLV_OP_READ 1
#define SNDRV_CTL_TLV_OP_WRITE 2
#define SNDRV_CTL_TLV_OP_CMD 3
#define VIRTIO_SND_S_OK 0
#define VIRTIO_SND_S_NOT_SUPP 1
#define VIRTIO_SND_S_IO_ERR 2
#define VIRTIO_SND_R_CTL_TLV_READ 0x0100
#define VIRTIO_SND_R_CTL_TLV_WRITE 0x0101
#define VIRTIO_SND_R_CTL_TLV_COMMAND 0x0102
#define VIRTIO_SND_R_CTL_ENUM_ITEMS 0x0103
#define VIRTIO_SND_R_CTL_INFO 0x0104
#define cpu_to_le32(value) ((u32)(value))
#define le32_to_cpu(value) ((u32)(value))
#define READ_ONCE(value) (value)
#define WRITE_ONCE(value, new_value) ((value) = (new_value))
#define WARN_ON(condition) ((void)(condition))
#define __user
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
#define guard(type) test_guard_##type
#define scoped_guard(type, lock) \
	for (unsigned int __guard_once = ((void)(lock), 1U); \
	     __guard_once; __guard_once = 0)

struct scatterlist {
	void *address;
	unsigned int length;
};
struct list_head { struct list_head *next, *prev; };
struct completion { bool done; };
struct virtio_device { struct { unsigned int unused; } dev; };
struct virtio_snd_hdr { u32 code; };
struct virtio_snd_ctl_hdr { struct virtio_snd_hdr hdr; u32 control_id; };
struct virtio_snd_query_info {
	struct virtio_snd_hdr hdr;
	u32 start_id;
	u32 count;
	u32 size;
};
struct virtio_snd_ctl_enum_item { u32 item_id; char name[32]; };
struct virtio_snd_ctl_info {
	union { struct { u32 items; } enumerated; } value;
};
struct virtio_kctl { struct virtio_snd_ctl_enum_item *items; };
struct virtio_snd;
struct virtio_snd_msg;
struct snd_kcontrol { unsigned long private_value; struct virtio_snd *chip; };
struct virtio_snd_queue { spinlock_t lock; struct virtqueue *vqueue; };
struct virtqueue {
	struct scatterlist descs[8][4];
	unsigned int nouts[8];
	unsigned int nins[8];
	void *cookies[8];
	unsigned int pending;
	unsigned int kicks;
};
struct virtio_snd {
	struct virtio_device *vdev;
	struct virtio_snd_queue ctl_queue;
	struct list_head ctl_msgs;
	bool ctl_msgs_blocked;
	struct virtio_snd_ctl_info *kctl_infos;
	struct virtio_kctl *kctls;
};

#define list_entry(pointer, type, member) container_of(pointer, type, member)
#define list_first_entry(head, type, member) \
	list_entry((head)->next, type, member)
#define list_for_each_entry(position, head, member) \
	for ((position) = list_entry((head)->next, typeof(*(position)), member); \
	     &(position)->member != (head); \
	     (position) = list_entry((position)->member.next, \
				 typeof(*(position)), member))

static inline void INIT_LIST_HEAD(struct list_head *head)
{
	head->next = head;
	head->prev = head;
}
static inline bool list_empty(const struct list_head *head)
{
	return head->next == head;
}
static inline void list_add_tail(struct list_head *entry, struct list_head *head)
{
	entry->prev = head->prev;
	entry->next = head;
	head->prev->next = entry;
	head->prev = entry;
}
static inline void list_del(struct list_head *entry)
{
	entry->next->prev = entry->prev;
	entry->prev->next = entry->next;
	INIT_LIST_HEAD(entry);
}

struct virtio_snd_queue *virtsnd_control_queue(struct virtio_snd *snd)
{
	return &snd->ctl_queue;
}
static inline struct virtio_snd *snd_kcontrol_chip(struct snd_kcontrol *control)
{
	return control->chip;
}
static inline void test_guard_spinlock_irqsave(spinlock_t *lock)
{
	(void)lock;
}
static inline void sg_init_one(struct scatterlist *sg, const void *address,
			       unsigned int length)
{
	sg->address = (void *)address;
	sg->length = length;
}
static inline void *sg_virt(struct scatterlist *sg) { return sg->address; }
static inline bool test_add_overflow_size(size_t a, size_t b, size_t *result)
{
	return __builtin_add_overflow(a, b, result);
}
static inline bool test_mul_overflow_size(size_t a, size_t b, size_t *result)
{
	return __builtin_mul_overflow(a, b, result);
}
#define check_add_overflow(a, b, result) \
	test_add_overflow_size((a), (b), (result))
#define check_mul_overflow(a, b, result) \
	test_mul_overflow_size((a), (b), (result))
static inline void refcount_inc(refcount_t *count) { ++*count; }
static inline bool refcount_dec_and_test(refcount_t *count)
{
	return --*count == 0;
}
static inline void refcount_set(refcount_t *count, unsigned int value)
{
	*count = value;
}
static inline void init_completion(struct completion *completion)
{
	completion->done = false;
}
static inline void complete(struct completion *completion)
{
	completion->done = true;
}
static inline unsigned int msecs_to_jiffies(unsigned int milliseconds)
{
	return milliseconds;
}
static inline bool virtqueue_kick_prepare(struct virtqueue *vqueue)
{
	return vqueue->pending != 0;
}
static inline void virtqueue_notify(struct virtqueue *vqueue)
{
	++vqueue->kicks;
}
static inline void dev_err(const void *device, const char *format, ...)
{
	(void)device;
	(void)format;
}
static inline void *devm_kcalloc(const void *device, size_t count, size_t size,
				 gfp_t flags)
{
	(void)device;
	(void)flags;
	return calloc(count, size);
}
static inline int copy_from_user(void *destination, const void *source,
				 size_t size)
{
	memcpy(destination, source, size);
	return 0;
}
static inline int copy_to_user(void *destination, const void *source,
			       size_t size)
{
	memcpy(destination, source, size);
	return 0;
}

struct allocation_record { void *base; size_t size; bool live; };
static struct allocation_record allocations[256];
static unsigned int allocation_count;
static unsigned int live_messages;
static size_t live_bytes;
static unsigned int message_allocations;
static unsigned int message_frees;
static size_t max_allocation_size = 1024U * 1024U;
static bool fail_next_message_allocation;
static void *kzalloc(size_t size, gfp_t flags)
{
	void *memory;
	(void)flags;
	if (fail_next_message_allocation) {
		fail_next_message_allocation = false;
		return NULL;
	}
	if (!size || size > max_allocation_size ||
	    allocation_count == sizeof(allocations) / sizeof(allocations[0]))
		return NULL;
	memory = calloc(1, size);
	if (!memory)
		return NULL;
	allocations[allocation_count++] = (struct allocation_record) {
		.base = memory, .size = size, .live = true,
	};
	++live_messages;
	live_bytes += size;
	++message_allocations;
	return memory;
}
static void kfree(void *memory)
{
	for (unsigned int i = 0; i < allocation_count; ++i) {
		if (allocations[i].base == memory && allocations[i].live) {
			allocations[i].live = false;
			--live_messages;
			live_bytes -= allocations[i].size;
			++message_frees;
			free(memory);
			return;
		}
	}
	free(memory);
}
static bool allocation_contains(const void *base, const void *address,
			       size_t length)
{
	uintptr_t start = (uintptr_t)base;
	uintptr_t target = (uintptr_t)address;
	for (unsigned int i = 0; i < allocation_count; ++i) {
		if (allocations[i].base == base && allocations[i].live)
			return target >= start && target - start <= allocations[i].size &&
				length <= allocations[i].size - (target - start);
	}
	return false;
}
static bool allocation_is_live(const void *base)
{
	for (unsigned int i = 0; i < allocation_count; ++i)
		if (allocations[i].base == base)
			return allocations[i].live;
	return false;
}

static unsigned int virtsnd_msg_timeout_ms = 1;
static enum { WAIT_TIMEOUT, WAIT_SUCCESS, WAIT_INTERRUPTED } wait_behavior;
static struct virtqueue *active_vqueue;
static bool check_outgoing_payload;
static const unsigned char *expected_outgoing_payload;
static size_t expected_outgoing_size;
static bool outgoing_payload_matches;
static bool response_payload_owned;
static unsigned char response_fill = 0xa0;
static unsigned int assertions;
static unsigned int failures;
static const char *active_test;
static void check(bool condition, const char *expression, int line)
{
	++assertions;
	if (!condition) {
		fprintf(stderr, "FAIL [%s] %s at line %d\n", active_test,
			expression, line);
		++failures;
	}
}
#define CHECK(condition) check((condition), #condition, __LINE__)

void virtsnd_ctl_msg_complete(struct virtio_snd_msg *msg);
static void device_complete_one(struct virtqueue *vqueue);
static long wait_for_completion_interruptible_timeout(
	struct completion *completion, unsigned int timeout)
{
	(void)timeout;
	if (wait_behavior == WAIT_SUCCESS) {
		device_complete_one(active_vqueue);
		return completion->done ? 1 : 0;
	}
	if (wait_behavior == WAIT_INTERRUPTED)
		return -ERESTARTSYS;
	return 0;
}
static int virtqueue_add_sgs(struct virtqueue *vqueue,
			     struct scatterlist **sgs, unsigned int nouts,
			     unsigned int nins, void *cookie, gfp_t flags)
{
	unsigned int index;
	(void)flags;
	if (vqueue->pending == 8)
		return -ENOSPC;
	index = vqueue->pending++;
	vqueue->nouts[index] = nouts;
	vqueue->nins[index] = nins;
	vqueue->cookies[index] = cookie;
	for (unsigned int i = 0; i < nouts + nins; ++i)
		vqueue->descs[index][i] = *sgs[i];
	return 0;
}

/* EXTRACTED_CONTROL_FUNCTIONS */

static void device_complete_one(struct virtqueue *vqueue)
{
	struct virtio_snd_msg *msg;
	struct virtio_snd_hdr *response;
	unsigned int index = 0;
	unsigned int nouts;
	unsigned int total;

	if (!vqueue->pending)
		abort();
	msg = vqueue->cookies[index];
	nouts = vqueue->nouts[index];
	total = nouts + vqueue->nins[index];
	if (check_outgoing_payload && nouts == 2) {
		struct scatterlist *payload = &vqueue->descs[index][1];
		outgoing_payload_matches =
			payload->length == expected_outgoing_size &&
			!memcmp(payload->address, expected_outgoing_payload,
				expected_outgoing_size);
	}
	response = vqueue->descs[index][nouts].address;
	response->code = cpu_to_le32(VIRTIO_SND_S_OK);
	if (vqueue->nins[index] == 2) {
		struct scatterlist *payload = &vqueue->descs[index][nouts + 1];
		unsigned char *bytes = payload->address;

		response_payload_owned = allocation_contains(msg, bytes,
							     payload->length);
		for (unsigned int i = 0; i < payload->length; ++i)
			bytes[i] = (unsigned char)(response_fill + i);
	}
	vqueue->pending--;
	for (unsigned int i = 0; i < vqueue->pending; ++i) {
		vqueue->cookies[i] = vqueue->cookies[i + 1];
		vqueue->nouts[i] = vqueue->nouts[i + 1];
		vqueue->nins[i] = vqueue->nins[i + 1];
		memcpy(vqueue->descs[i], vqueue->descs[i + 1],
		       sizeof(vqueue->descs[i]));
	}
	(void)total;
	virtsnd_ctl_msg_complete(msg);
}

static void device_touch_late(struct virtqueue *vqueue, bool device_writes)
{
	struct scatterlist *payload;
	struct virtio_snd_msg *msg;
	unsigned int nouts;
	unsigned int index = 0;

	if (!vqueue->pending)
		abort();
	msg = vqueue->cookies[index];
	nouts = vqueue->nouts[index];
	payload = &vqueue->descs[index][device_writes ? nouts + 1 : 1];
	if (device_writes) {
		volatile unsigned char *bytes = payload->address;
		response_payload_owned = allocation_contains(msg, payload->address,
							     payload->length);
		for (unsigned int i = 0; i < payload->length; ++i)
			bytes[i] = (unsigned char)(0x60U + i);
	} else {
		volatile unsigned char value =
			((volatile unsigned char *)payload->address)[0];
		(void)value;
	}
	vqueue->pending--;
	virtsnd_ctl_msg_complete(msg);
}

static void reset_fixture(struct virtio_snd *snd, struct virtio_device *vdev,
			  struct virtqueue *vqueue)
{
	memset(snd, 0, sizeof(*snd));
	memset(vdev, 0, sizeof(*vdev));
	memset(vqueue, 0, sizeof(*vqueue));
	INIT_LIST_HEAD(&snd->ctl_msgs);
	snd->vdev = vdev;
	snd->ctl_queue.vqueue = vqueue;
	active_vqueue = vqueue;
	wait_behavior = WAIT_TIMEOUT;
	check_outgoing_payload = false;
	outgoing_payload_matches = false;
	response_payload_owned = false;
	response_fill = 0xa0;
	fail_next_message_allocation = false;
}

#ifdef TEST_BASELINE
static void expect_baseline_uaf(const char *scenario)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct snd_kcontrol control = { .private_value = 17 };
	unsigned int *user_tlv = malloc(16);
	unsigned char *info = malloc(24);
	int rc;

	active_test = scenario;
	if (!user_tlv || !info)
		abort();
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	for (unsigned int i = 0; i < 4; ++i)
		user_tlv[i] = 0x11223344U + i;
	memset(info, 0x5a, 24);
	if (!strcmp(scenario, "tlv-read")) {
		rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_READ,
					 16, user_tlv);
		CHECK(rc == -ETIMEDOUT);
		free(user_tlv);
		device_touch_late(&vqueue, true);
	} else if (!strcmp(scenario, "tlv-write")) {
		rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
					 16, user_tlv);
		CHECK(rc == -ETIMEDOUT);
		free(user_tlv);
		device_touch_late(&vqueue, false);
	} else if (!strcmp(scenario, "query-info")) {
		rc = virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 0,
					   3, 8, info);
		CHECK(rc == -ETIMEDOUT);
		free(info);
		device_touch_late(&vqueue, true);
	} else {
		fprintf(stderr, "unknown baseline scenario: %s\n", scenario);
		exit(2);
	}
}
#endif

#ifdef TEST_CANDIDATE
static void test_tlv_read_success_and_owned_timeout(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct snd_kcontrol control = { .private_value = 17 };
	unsigned char user_tlv[12];
	int rc;

	active_test = "tlv-read-success-copyout";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	memset(user_tlv, 0xcc, sizeof(user_tlv));
	wait_behavior = WAIT_SUCCESS;
	rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_READ,
				 sizeof(user_tlv), (unsigned int *)user_tlv);
	CHECK(rc == 0);
	CHECK(vqueue.pending == 0);
	CHECK(vqueue.nouts[0] == 1 && vqueue.nins[0] == 2);
	CHECK(vqueue.descs[0][2].length == sizeof(user_tlv));
	CHECK(response_payload_owned);
	CHECK(user_tlv[0] == response_fill);
	CHECK(user_tlv[sizeof(user_tlv) - 1] ==
	      (unsigned char)(response_fill + sizeof(user_tlv) - 1));
	CHECK(live_messages == 0);

	active_test = "tlv-read-timeout-user-buffer-unchanged";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	memset(user_tlv, 0xcc, sizeof(user_tlv));
	rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_READ,
				 sizeof(user_tlv), (unsigned int *)user_tlv);
	CHECK(rc == -ETIMEDOUT);
	CHECK(vqueue.pending == 1);
	CHECK(!memcmp(user_tlv, "\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc",
		      sizeof(user_tlv)));
	CHECK(allocation_contains(vqueue.cookies[0],
				  vqueue.descs[0][2].address,
				  vqueue.descs[0][2].length));
	device_touch_late(&vqueue, true);
	CHECK(vqueue.pending == 0);
	CHECK(response_payload_owned);
	CHECK(live_messages == 0);

	active_test = "tlv-read-interrupted-user-buffer-unchanged";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	memset(user_tlv, 0xcc, sizeof(user_tlv));
	wait_behavior = WAIT_INTERRUPTED;
	rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_READ,
				 sizeof(user_tlv), (unsigned int *)user_tlv);
	CHECK(rc == -ERESTARTSYS);
	CHECK(vqueue.pending == 1);
	CHECK(!memcmp(user_tlv, "\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc",
		      sizeof(user_tlv)));
	CHECK(allocation_contains(vqueue.cookies[0],
				  vqueue.descs[0][2].address,
				  vqueue.descs[0][2].length));
	device_touch_late(&vqueue, true);
	CHECK(response_payload_owned);
	CHECK(live_messages == 0);
}

static void test_tlv_write_copyin_timeout_and_interruption(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct snd_kcontrol control = { .private_value = 23 };
	unsigned char input[16];
	unsigned char original[sizeof(input)];
	int rc;

	for (unsigned int i = 0; i < sizeof(input); ++i)
		input[i] = (unsigned char)(0x31 + i);
	memcpy(original, input, sizeof(input));
	active_test = "tlv-write-success-copyin-direction-and-size";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	check_outgoing_payload = true;
	expected_outgoing_payload = input;
	expected_outgoing_size = sizeof(input);
	wait_behavior = WAIT_SUCCESS;
	rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				 sizeof(input), (unsigned int *)input);
	CHECK(rc == 0);
	CHECK(outgoing_payload_matches);
	CHECK(vqueue.nouts[0] == 2 && vqueue.nins[0] == 1);
	CHECK(vqueue.descs[0][1].length == sizeof(input));
	CHECK(vqueue.pending == 0);
	CHECK(live_messages == 0);

	active_test = "tlv-write-interrupted-late-device-read";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	wait_behavior = WAIT_INTERRUPTED;
	rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				 sizeof(input), (unsigned int *)input);
	CHECK(rc == -ERESTARTSYS);
	CHECK(vqueue.pending == 1);
	CHECK(vqueue.nouts[0] == 2 && vqueue.nins[0] == 1);
	CHECK(vqueue.descs[0][1].length == sizeof(input));
	CHECK(allocation_contains(vqueue.cookies[0],
				  vqueue.descs[0][1].address, sizeof(input)));
	device_touch_late(&vqueue, false);
	CHECK(live_messages == 0);

	active_test = "tlv-write-timeout-user-buffer-reuse";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				  sizeof(input), (unsigned int *)input) == -ETIMEDOUT);
	CHECK(vqueue.pending == 1);
	CHECK(vqueue.nouts[0] == 2 && vqueue.nins[0] == 1);
	CHECK(vqueue.descs[0][1].length == sizeof(input));
	CHECK(allocation_contains(vqueue.cookies[0],
				  vqueue.descs[0][1].address, sizeof(input)));
	memset(input, 0xee, sizeof(input));
	CHECK(!memcmp(vqueue.descs[0][1].address, original, sizeof(original)));
	device_touch_late(&vqueue, false);
	CHECK(live_messages == 0);
}

static void test_tlv_command_timeout_next_operation(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct snd_kcontrol control = { .private_value = 29 };
	unsigned char input[8] = { 1, 3, 5, 7, 9, 11, 13, 15 };
	int rc;

	active_test = "tlv-command-timeout-then-next-normal";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	rc = virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_CMD,
				 sizeof(input), (unsigned int *)input);
	CHECK(rc == -ETIMEDOUT);
	CHECK(vqueue.pending == 1);
	CHECK(vqueue.nouts[0] == 2 && vqueue.nins[0] == 1);
	device_touch_late(&vqueue, false);
	CHECK(live_messages == 0);
	wait_behavior = WAIT_SUCCESS;
	CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_CMD,
				  sizeof(input), (unsigned int *)input) == 0);
	CHECK(vqueue.pending == 0);
	CHECK(live_messages == 0);
}

static void test_query_info_success_timeout_and_bounds(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	unsigned char info[12];
	int rc;

	active_test = "query-info-success-copyout-size";
	reset_fixture(&snd, &vdev, &vqueue);
	memset(info, 0xdd, sizeof(info));
	wait_behavior = WAIT_SUCCESS;
	rc = virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 4, 3, 4,
				    info);
	CHECK(rc == 0);
	CHECK(response_payload_owned);
	CHECK(info[0] == response_fill && info[11] == response_fill + 11);
	CHECK(vqueue.nouts[0] == 1 && vqueue.nins[0] == 2);
	CHECK(vqueue.descs[0][2].length == sizeof(info));
	CHECK(live_messages == 0);

	active_test = "query-info-timeout-late-dma-after-dest-free";
	reset_fixture(&snd, &vdev, &vqueue);
	unsigned char *expired_info = malloc(sizeof(info));
	CHECK(expired_info != NULL);
	memset(expired_info, 0x4d, sizeof(info));
	rc = virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 0, 3, 4,
				    expired_info);
	CHECK(rc == -ETIMEDOUT);
	CHECK(allocation_contains(vqueue.cookies[0],
				  vqueue.descs[0][2].address,
				  vqueue.descs[0][2].length));
	free(expired_info);
	device_touch_late(&vqueue, true);
	CHECK(response_payload_owned);
	CHECK(live_messages == 0);

	active_test = "query-info-reject-invalid-sizes-before-queue";
	reset_fixture(&snd, &vdev, &vqueue);
	CHECK(virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 0, -1, 4,
				     info) == -EINVAL);
	CHECK(virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 0, 1,
				     (size_t)UINT_MAX + 1, info) == -EOVERFLOW);
	CHECK(virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 0, 2,
				     UINT_MAX, info) == -EOVERFLOW);
	CHECK(virtsnd_ctl_query_info(&snd, VIRTIO_SND_R_CTL_INFO, 0, 1, 4,
				     NULL) == -EINVAL);
	CHECK(vqueue.pending == 0);
	CHECK(live_messages == 0);
	CHECK(virtsnd_ctl_msg_alloc(0, sizeof(struct virtio_snd_hdr), 0, 0,
				    GFP_KERNEL) == NULL);
	CHECK(virtsnd_ctl_msg_alloc(sizeof(struct virtio_snd_hdr),
				    sizeof(struct virtio_snd_hdr), 0,
				    (size_t)UINT_MAX + 1, GFP_KERNEL) == NULL);
	CHECK(live_messages == 0);
}

static void test_enum_items_copyout_and_timeout(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct virtio_snd_ctl_info kinfo[1] = { 0 };
	struct virtio_kctl kctls[1] = { 0 };
	unsigned int n = 3;
	int rc;

	active_test = "enum-items-success-copyout";
	reset_fixture(&snd, &vdev, &vqueue);
	kinfo[0].value.enumerated.items = n;
	snd.kctl_infos = kinfo;
	snd.kctls = kctls;
	wait_behavior = WAIT_SUCCESS;
	rc = virtsnd_kctl_get_enum_items(&snd, 0);
	CHECK(rc == 0);
	CHECK(kctls[0].items != NULL);
	CHECK(kctls[0].items[0].item_id == 0xa3a2a1a0U);
	CHECK(vqueue.nouts[0] == 1 && vqueue.nins[0] == 2);
	CHECK(vqueue.descs[0][2].length == n * sizeof(*kctls[0].items));
	CHECK(response_payload_owned);
	free(kctls[0].items);
	kctls[0].items = NULL;
	CHECK(live_messages == 0);

	active_test = "enum-items-timeout-output-unchanged-late-dma";
	reset_fixture(&snd, &vdev, &vqueue);
	kinfo[0].value.enumerated.items = n;
	snd.kctl_infos = kinfo;
	snd.kctls = kctls;
	wait_behavior = WAIT_TIMEOUT;
	rc = virtsnd_kctl_get_enum_items(&snd, 0);
	CHECK(rc == -ETIMEDOUT);
	CHECK(kctls[0].items != NULL);
	CHECK(kctls[0].items[0].item_id == 0);
	CHECK(allocation_contains(vqueue.cookies[0],
				  vqueue.descs[0][2].address,
				  vqueue.descs[0][2].length));
	device_touch_late(&vqueue, true);
	CHECK(kctls[0].items[0].item_id == 0);
	CHECK(response_payload_owned);
	free(kctls[0].items);
	kctls[0].items = NULL;
	CHECK(live_messages == 0);

	active_test = "enum-items-count-size-bounds";
	reset_fixture(&snd, &vdev, &vqueue);
	kinfo[0].value.enumerated.items = UINT_MAX;
	snd.kctl_infos = kinfo;
	snd.kctls = kctls;
	CHECK(virtsnd_kctl_get_enum_items(&snd, 0) == -EOVERFLOW);
	CHECK(vqueue.pending == 0);
	CHECK(live_messages == 0);
}

static void test_retained_queue_bound_and_reset_cleanup(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct snd_kcontrol control = { .private_value = 41 };
	unsigned char input[8] = { 9, 8, 7, 6, 5, 4, 3, 2 };
	unsigned int bounded_messages;
	size_t bounded_bytes;

	active_test = "bounded-timeouts-reset-reopen-next-normal";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	for (unsigned int i = 0; i < 8; ++i)
		CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
					  sizeof(input), (unsigned int *)input) ==
		      -ETIMEDOUT);
	CHECK(vqueue.pending == 8);
	CHECK(live_messages == 8);
	bounded_messages = live_messages;
	bounded_bytes = live_bytes;
	CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				  sizeof(input), (unsigned int *)input) == -ENOSPC);
	CHECK(vqueue.pending == 8);
	CHECK(live_messages == bounded_messages);
	CHECK(live_bytes == bounded_bytes);

	virtsnd_ctl_msg_cancel_all(&snd);
	CHECK(snd.ctl_msgs_blocked);
	/* Simulate virtio_reset_device() + del_vqs() before driver cleanup. */
	vqueue.pending = 0;
	virtsnd_ctl_msg_cleanup(&snd);
	CHECK(list_empty(&snd.ctl_msgs));
	CHECK(live_messages == 0);
	virtsnd_ctl_msg_cleanup(&snd);
	CHECK(list_empty(&snd.ctl_msgs));
	CHECK(live_messages == 0);
	CHECK(message_allocations == message_frees);
	virtsnd_ctl_msg_enable(&snd);
	CHECK(!snd.ctl_msgs_blocked);
	wait_behavior = WAIT_SUCCESS;
	CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				  sizeof(input), (unsigned int *)input) == 0);
	CHECK(vqueue.pending == 0);
	CHECK(live_messages == 0);
	CHECK(message_allocations == message_frees);
}

static void test_control_allocation_failure_then_recovery(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct snd_kcontrol control = { .private_value = 43 };
	unsigned char input[8] = { 2, 4, 6, 8, 10, 12, 14, 16 };

	active_test = "message-allocation-failure-no-queue-leak";
	reset_fixture(&snd, &vdev, &vqueue);
	control.chip = &snd;
	fail_next_message_allocation = true;
	CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				  sizeof(input), (unsigned int *)input) == -ENOMEM);
	CHECK(vqueue.pending == 0);
	CHECK(list_empty(&snd.ctl_msgs));
	CHECK(live_messages == 0);

	active_test = "message-allocation-recovery-next-normal-operation";
	wait_behavior = WAIT_SUCCESS;
	CHECK(virtsnd_kctl_tlv_op(&control, SNDRV_CTL_TLV_OP_WRITE,
				  sizeof(input), (unsigned int *)input) == 0);
	CHECK(vqueue.pending == 0);
	CHECK(list_empty(&snd.ctl_msgs));
	CHECK(live_messages == 0);
	CHECK(message_allocations == message_frees);
}

static void test_cancel_keeps_queue_ref_for_late_callback(void)
{
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue vqueue;
	struct virtio_snd_msg *msg;

	active_test = "timeout-cancel-late-callback-retains-message";
	reset_fixture(&snd, &vdev, &vqueue);
	msg = virtsnd_ctl_msg_alloc(sizeof(struct virtio_snd_ctl_hdr),
				    sizeof(struct virtio_snd_hdr), 0, 8,
				    GFP_KERNEL);
	CHECK(msg != NULL);
	CHECK(virtsnd_ctl_msg_send(&snd, msg, false) == -ETIMEDOUT);
	CHECK(vqueue.pending == 1);
	CHECK(live_messages == 1);
	virtsnd_ctl_msg_cancel_all(&snd);
	CHECK(snd.ctl_msgs_blocked);
	CHECK(!list_empty(&snd.ctl_msgs));
	CHECK(live_messages == 1);
	device_touch_late(&vqueue, true);
	CHECK(list_empty(&snd.ctl_msgs));
	CHECK(vqueue.pending == 0);
	CHECK(response_payload_owned);
	CHECK(live_messages == 0);
}
#endif

int main(int argc, char **argv)
{
#ifdef TEST_BASELINE
	if (argc != 2) {
		fprintf(stderr, "expected baseline scenario\n");
		return 2;
	}
	expect_baseline_uaf(argv[1]);
	return 0;
#else
	(void)argc;
	(void)argv;
	test_tlv_read_success_and_owned_timeout();
	test_tlv_write_copyin_timeout_and_interruption();
	test_tlv_command_timeout_next_operation();
	test_query_info_success_timeout_and_bounds();
	test_enum_items_copyout_and_timeout();
	test_retained_queue_bound_and_reset_cleanup();
	test_control_allocation_failure_then_recovery();
	test_cancel_keeps_queue_ref_for_late_callback();
	if (failures) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertions, failures);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertions);
	return 0;
#endif
}

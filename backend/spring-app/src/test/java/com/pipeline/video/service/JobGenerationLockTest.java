package com.pipeline.video.service;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class JobGenerationLockTest {

    @Test
    void secondAcquireForSameJobAndStageFailsUntilReleased() {
        JobGenerationLock lock = new JobGenerationLock();

        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue();
        assertThat(lock.tryAcquire(1L, "SCRIPT")).isFalse();

        lock.release(1L, "SCRIPT");

        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue();
    }

    @Test
    void differentJobsOrStagesDoNotBlockEachOther() {
        JobGenerationLock lock = new JobGenerationLock();

        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue();
        assertThat(lock.tryAcquire(2L, "SCRIPT")).isTrue();
        assertThat(lock.tryAcquire(1L, "TTS")).isTrue();
    }

    @Test
    void releaseOfUnacquiredKeyIsHarmless() {
        JobGenerationLock lock = new JobGenerationLock();

        lock.release(99L, "SCRIPT");

        assertThat(lock.tryAcquire(99L, "SCRIPT")).isTrue();
    }
}

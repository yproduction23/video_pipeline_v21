package com.pipeline.video.service;

import com.pipeline.video.domain.ChannelProfile;
import com.pipeline.video.domain.ContentNature;
import com.pipeline.video.dto.CreateJobRequest;
import com.pipeline.video.repository.ChannelProfileRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobServiceContentNatureTest {

    @Mock
    private ChannelProfileRepository channelProfileRepository;

    @InjectMocks
    private JobService jobService;

    private CreateJobRequest request(String channelId, ContentNature nature) {
        CreateJobRequest request = new CreateJobRequest();
        request.setChannelId(channelId);
        request.setContentNature(nature);
        return request;
    }

    @Test
    void requestValueWinsOverChannelDefault() {
        assertThat(jobService.resolveContentNature(request("ch", ContentNature.STORY)))
                .isEqualTo(ContentNature.STORY);
    }

    @Test
    void fallsBackToChannelDefault() {
        ChannelProfile channel = new ChannelProfile();
        channel.setContentNature(ContentNature.EXPLAINER);
        when(channelProfileRepository.findById("ch")).thenReturn(Optional.of(channel));

        assertThat(jobService.resolveContentNature(request("ch", null))).isEqualTo(ContentNature.EXPLAINER);
    }

    @Test
    void defaultsToFactualWhenChannelHasNoNature() {
        when(channelProfileRepository.findById("ch")).thenReturn(Optional.of(new ChannelProfile()));

        assertThat(jobService.resolveContentNature(request("ch", null))).isEqualTo(ContentNature.FACTUAL);
    }

    @Test
    void defaultsToFactualWithoutChannel() {
        assertThat(jobService.resolveContentNature(request(null, null))).isEqualTo(ContentNature.FACTUAL);
        assertThat(jobService.resolveContentNature(request(" ", null))).isEqualTo(ContentNature.FACTUAL);
    }
}

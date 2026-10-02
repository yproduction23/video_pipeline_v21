package com.pipeline.video.controller;

import com.pipeline.video.domain.ChannelProfile;
import com.pipeline.video.dto.CharacterLibraryGenerateRequest;
import com.pipeline.video.repository.ChannelProfileRepository;
import com.pipeline.video.repository.ElevenLabsVoiceRepository;
import com.pipeline.video.service.FastApiClient;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ChannelProfileControllerTest {

    private final ChannelProfileRepository channelProfileRepository = mock(ChannelProfileRepository.class);
    private final FastApiClient fastApiClient = mock(FastApiClient.class);
    private final ElevenLabsVoiceRepository elevenLabsVoiceRepository = mock(ElevenLabsVoiceRepository.class);
    private final ChannelProfileController controller =
            new ChannelProfileController(channelProfileRepository, fastApiClient, elevenLabsVoiceRepository);

    private ChannelProfile channelWithBlankIdentityReference() {
        return ChannelProfile.builder()
                .channelId("channel_b")
                .channelName("채널 B")
                .characterStylePrompt("friendly Korean finance educator, money bundle mascot, editorial 2D comic style")
                .build();
    }

    @Test
    void generatingTheLibraryFillsInABlankCharacterImagePathWithTheNeutralPose() {
        ChannelProfile profile = channelWithBlankIdentityReference();
        when(channelProfileRepository.findById("channel_b")).thenReturn(Optional.of(profile));
        when(fastApiClient.generateCharacterLibrary(anyString(), anyString(), anyBoolean(), anyBoolean(), any()))
                .thenReturn(new java.util.HashMap<>(Map.of(
                        "poses_dir", "/app/data/characters/channel_b/poses",
                        "generated", List.of("neutral"))));

        CharacterLibraryGenerateRequest request = new CharacterLibraryGenerateRequest();
        request.setRegenerate(true);

        controller.generateCharacterLibrary("channel_b", request);

        assertThat(profile.getCharacterImagePath())
                .isEqualTo("/app/data/characters/channel_b/poses/neutral.png");
    }

    @Test
    void generatingTheLibraryDoesNotOverwriteAnOperatorChosenCharacterImagePath() {
        ChannelProfile profile = channelWithBlankIdentityReference();
        profile.setCharacterImagePath("/app/data/characters/channel_b/poses/explaining.png");
        when(channelProfileRepository.findById("channel_b")).thenReturn(Optional.of(profile));
        when(fastApiClient.generateCharacterLibrary(anyString(), anyString(), anyBoolean(), anyBoolean(), any()))
                .thenReturn(new java.util.HashMap<>(Map.of(
                        "poses_dir", "/app/data/characters/channel_b/poses",
                        "generated", List.of("neutral"))));

        CharacterLibraryGenerateRequest request = new CharacterLibraryGenerateRequest();
        request.setRegenerate(true);

        controller.generateCharacterLibrary("channel_b", request);

        assertThat(profile.getCharacterImagePath())
                .isEqualTo("/app/data/characters/channel_b/poses/explaining.png");
    }
}

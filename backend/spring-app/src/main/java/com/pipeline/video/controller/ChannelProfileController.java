package com.pipeline.video.controller;

import com.pipeline.video.domain.ChannelProfile;
import com.pipeline.video.domain.ElevenLabsVoice;
import com.pipeline.video.dto.CharacterLibraryGenerateRequest;
import com.pipeline.video.repository.ChannelProfileRepository;
import com.pipeline.video.repository.ElevenLabsVoiceRepository;
import com.pipeline.video.service.FastApiClient;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/channels")
@RequiredArgsConstructor
public class ChannelProfileController {

    private final ChannelProfileRepository channelProfileRepository;
    private final FastApiClient fastApiClient;
    private final ElevenLabsVoiceRepository elevenLabsVoiceRepository;

    /**
     * Product-owned audition voices.  They are kept in the account as real
     * ElevenLabs voices, but their reviewed audition files are also stored in
     * the shared /app/data volume so the UI does not depend on a remote preview
     * URL or on a second API call.
     */
    private void ensureProductVoices() {
        ensureProductVoice(
                "dlKJ5VptCbYxal4doUO5",
                "IVC 경제 뉴스 앵커 (기본)",
                "custom",
                "사용자 제공 음성에서 권한 확인 후 생성한 한국어 경제 뉴스 앵커 음성",
                "/api/channels/voices/preview/dlKJ5VptCbYxal4doUO5",
                "/api/channels/voices/audition/dlKJ5VptCbYxal4doUO5"
        );
        ensureProductVoice(
                "KEo3H2UC7XebTpHFgc3B",
                "오리지널 금융 앵커",
                "generated",
                "식별 가능한 인물을 모방하지 않은 원본 금융 뉴스 내레이션 음성",
                "/api/channels/voices/preview/KEo3H2UC7XebTpHFgc3B",
                "/api/channels/voices/audition/KEo3H2UC7XebTpHFgc3B"
        );
    }

    private void ensureProductVoice(String voiceId, String name, String category,
                                    String description, String previewUrl, String auditionUrl) {
        ElevenLabsVoice voice = elevenLabsVoiceRepository.findById(voiceId)
                .orElseGet(ElevenLabsVoice::new);
        voice.setVoiceId(voiceId);
        voice.setName(name);
        voice.setCategory(category);
        voice.setDescription(description);
        voice.setPreviewUrl(previewUrl);
        voice.setAuditionUrl(auditionUrl);
        elevenLabsVoiceRepository.save(voice);
    }

    @GetMapping
    public ResponseEntity<List<ChannelProfile>> getAll() {
        return ResponseEntity.ok(channelProfileRepository.findAll());
    }

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(ChannelProfileController.class);

    @GetMapping("/voices")
    public ResponseEntity<List<ElevenLabsVoice>> getVoices() {
        List<ElevenLabsVoice> dbVoices = elevenLabsVoiceRepository.findAll();
        if (dbVoices.size() < 21) {
            List<Map<String, Object>> apiVoices = fastApiClient.getElevenLabsVoices();
            for (Map<String, Object> av : apiVoices) {
                String voiceId = (String) av.get("voice_id");
                String name = (String) av.get("name");
                String category = (String) av.get("category");
                String description = (String) av.get("description");
                String extPreviewUrl = (String) av.get("preview_url");

                // Download preview locally and change URL
                String previewUrl = null;
                if (extPreviewUrl != null && !extPreviewUrl.isBlank()) {
                    downloadPreviewFile(voiceId, extPreviewUrl);
                    previewUrl = "/api/channels/voices/preview/" + voiceId;
                }

                String auditionUrl = null;
                java.io.File dir = new java.io.File("/app/data/voice_auditions");
                if (dir.exists() && dir.isDirectory()) {
                    java.io.File[] files = dir.listFiles((d, fName) -> fName.endsWith("_" + voiceId + ".mp3"));
                    if (files != null && files.length > 0) {
                        auditionUrl = "/api/channels/voices/audition/" + voiceId;
                    }
                }

                ElevenLabsVoice ev = ElevenLabsVoice.builder()
                        .voiceId(voiceId)
                        .name(name)
                        .category(category)
                        .description(description)
                        .previewUrl(previewUrl)
                        .auditionUrl(auditionUrl)
                        .build();
                elevenLabsVoiceRepository.save(ev);
            }
            dbVoices = elevenLabsVoiceRepository.findAll();
        } else {
            boolean updated = false;
            for (ElevenLabsVoice ev : dbVoices) {
                // Migrate external preview URL to local endpoint
                if (ev.getPreviewUrl() != null && ev.getPreviewUrl().startsWith("http")) {
                    String extUrl = ev.getPreviewUrl();
                    downloadPreviewFile(ev.getVoiceId(), extUrl);
                    ev.setPreviewUrl("/api/channels/voices/preview/" + ev.getVoiceId());
                    elevenLabsVoiceRepository.save(ev);
                    updated = true;
                }

                if (ev.getAuditionUrl() == null) {
                    String voiceId = ev.getVoiceId();
                    java.io.File dir = new java.io.File("/app/data/voice_auditions");
                    if (dir.exists() && dir.isDirectory()) {
                        java.io.File[] files = dir.listFiles((d, fName) -> fName.endsWith("_" + voiceId + ".mp3"));
                        if (files != null && files.length > 0) {
                            ev.setAuditionUrl("/api/channels/voices/audition/" + voiceId);
                            elevenLabsVoiceRepository.save(ev);
                            updated = true;
                        }
                    }
                }
            }
            if (updated) {
                dbVoices = elevenLabsVoiceRepository.findAll();
            }
        }
        ensureProductVoices();
        return ResponseEntity.ok(elevenLabsVoiceRepository.findAll());
    }

    @PostMapping("/voices/sync")
    public ResponseEntity<List<ElevenLabsVoice>> syncVoices() {
        List<Map<String, Object>> apiVoices = fastApiClient.getElevenLabsVoices();
        for (Map<String, Object> av : apiVoices) {
            String voiceId = (String) av.get("voice_id");
            String name = (String) av.get("name");
            String category = (String) av.get("category");
            String description = (String) av.get("description");
            String extPreviewUrl = (String) av.get("preview_url");

            String previewUrl = null;
            if (extPreviewUrl != null && !extPreviewUrl.isBlank()) {
                downloadPreviewFile(voiceId, extPreviewUrl);
                previewUrl = "/api/channels/voices/preview/" + voiceId;
            }

            String auditionUrl = null;
            java.io.File dir = new java.io.File("/app/data/voice_auditions");
            if (dir.exists() && dir.isDirectory()) {
                java.io.File[] files = dir.listFiles((d, fName) -> fName.endsWith("_" + voiceId + ".mp3"));
                if (files != null && files.length > 0) {
                    auditionUrl = "/api/channels/voices/audition/" + voiceId;
                }
            }

            ElevenLabsVoice ev = elevenLabsVoiceRepository.findById(voiceId)
                    .orElse(new ElevenLabsVoice());
            ev.setVoiceId(voiceId);
            ev.setName(name);
            ev.setCategory(category);
            ev.setDescription(description);
            ev.setPreviewUrl(previewUrl);
            ev.setAuditionUrl(auditionUrl);
            elevenLabsVoiceRepository.save(ev);
        }
        ensureProductVoices();
        return ResponseEntity.ok(elevenLabsVoiceRepository.findAll());
    }

    @GetMapping("/voices/audition/{voiceId}")
    public ResponseEntity<org.springframework.core.io.Resource> getAuditionAudio(@PathVariable String voiceId) {
        java.io.File dir = new java.io.File("/app/data/voice_auditions");
        if (dir.exists() && dir.isDirectory()) {
            java.io.File[] files = dir.listFiles((d, name) -> name.endsWith("_" + voiceId + ".mp3"));
            if (files != null && files.length > 0) {
                return ResponseEntity.ok()
                        .contentType(org.springframework.http.MediaType.parseMediaType("audio/mpeg"))
                        .body(new org.springframework.core.io.FileSystemResource(files[0]));
            }
        }
        return ResponseEntity.notFound().build();
    }

    @PostMapping("/voices/preview")
    public ResponseEntity<byte[]> previewVoice(@RequestBody Map<String, String> request) {
        String voiceId = request.get("voiceId");
        String text = request.get("text");
        if (voiceId == null || voiceId.isBlank() || text == null || text.isBlank() || text.length() > 100) {
            return ResponseEntity.badRequest().build();
        }
        byte[] audio = fastApiClient.previewTts(voiceId.trim(), text.trim());
        return ResponseEntity.ok()
                .contentType(org.springframework.http.MediaType.parseMediaType("audio/mpeg"))
                .body(audio);
    }

    @GetMapping("/voices/preview/{voiceId}")
    public ResponseEntity<org.springframework.core.io.Resource> getPreviewAudio(@PathVariable String voiceId) {
        java.io.File file = new java.io.File("/app/data/voice_previews/" + voiceId + ".mp3");
        if (file.exists() && file.isFile()) {
            return ResponseEntity.ok()
                    .contentType(org.springframework.http.MediaType.parseMediaType("audio/mpeg"))
                    .body(new org.springframework.core.io.FileSystemResource(file));
        }
        return ResponseEntity.notFound().build();
    }

    private void downloadPreviewFile(String voiceId, String extPreviewUrl) {
        if (extPreviewUrl == null || extPreviewUrl.isBlank()) return;
        try {
            java.io.File dir = new java.io.File("/app/data/voice_previews");
            if (!dir.exists()) dir.mkdirs();

            java.io.File file = new java.io.File(dir, voiceId + ".mp3");
            if (file.exists()) return; // Already cached

            log.info("ElevenLabs 공식 미리듣기 다운로드 중... voiceId={}, url={}", voiceId, extPreviewUrl);
            java.net.URL url = new java.net.URL(extPreviewUrl);
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);

            if (conn.getResponseCode() == 200) {
                try (java.io.InputStream in = conn.getInputStream();
                     java.io.FileOutputStream out = new java.io.FileOutputStream(file)) {
                    in.transferTo(out);
                    log.info("공식 미리듣기 다운로드 완료: {}", file.getAbsolutePath());
                }
            } else {
                log.warn("공식 미리듣기 다운로드 실패: HTTP {}", conn.getResponseCode());
            }
        } catch (Exception e) {
            log.error("공식 미리듣기 다운로드 중 예외 발생: {}", e.getMessage());
        }
    }

    @GetMapping("/{id}")
    public ResponseEntity<ChannelProfile> getOne(@PathVariable String id) {
        return channelProfileRepository.findById(id)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @GetMapping("/{id}/character-library")
    public ResponseEntity<Map<String, Object>> getCharacterLibrary(@PathVariable String id) {
        if (!channelProfileRepository.existsById(id)) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(fastApiClient.getCharacterLibraryStatus(id));
    }

    @PostMapping("/{id}/character-library")
    public ResponseEntity<Map<String, Object>> generateCharacterLibrary(
            @PathVariable String id,
            @RequestBody CharacterLibraryGenerateRequest request) {
        ChannelProfile profile = channelProfileRepository.findById(id).orElse(null);
        if (profile == null) {
            return ResponseEntity.notFound().build();
        }

        String description = request.getCharacterDescription();
        if (description == null || description.isBlank()) {
            description = profile.getCharacterStylePrompt();
        }
        if (description == null || description.isBlank()) {
            return ResponseEntity.badRequest().build();
        }

        boolean hasPoseNames = request.getPoseNames() != null && !request.getPoseNames().isEmpty();
        // 특정 포즈만 지정했다면 교정이 목적이므로, 이미 있는 파일이라도
        // 반드시 다시 만든다(그렇지 않으면 깨진 기존 파일을 그대로 건너뛴다).
        boolean effectiveRegenerate = request.isRegenerate() || hasPoseNames;
        Map<String, Object> result = fastApiClient.generateCharacterLibrary(
                id, description, effectiveRegenerate, request.isIncludeRoleCostumes(), request.getPoseNames());
        Object posesDir = result.get("poses_dir");
        if (posesDir instanceof String path && !path.isBlank()) {
            profile.setCharacterPosesDir(path);
        }
        if (profile.getCharacterStylePrompt() == null || profile.getCharacterStylePrompt().isBlank()) {
            profile.setCharacterStylePrompt(description);
        }
        channelProfileRepository.save(profile);
        result.put("profile_updated", true);
        return ResponseEntity.ok(result);
    }

    @GetMapping("/{id}/character-library/pose/{pose}")
    public ResponseEntity<byte[]> getCharacterPose(@PathVariable String id, @PathVariable String pose) {
        if (!channelProfileRepository.existsById(id)) {
            return ResponseEntity.notFound().build();
        }
        ResponseEntity<byte[]> response = fastApiClient.getCharacterPose(id, pose);
        return ResponseEntity.status(response.getStatusCode())
                .contentType(org.springframework.http.MediaType.IMAGE_PNG)
                .body(response.getBody());
    }

    @PostMapping
    public ResponseEntity<ChannelProfile> save(@RequestBody ChannelProfile profile) {
        if (profile.getChannelId() == null || profile.getChannelId().isBlank()) {
            return ResponseEntity.badRequest().build();
        }
        return ResponseEntity.ok(channelProfileRepository.save(profile));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable String id) {
        if (!channelProfileRepository.existsById(id)) {
            return ResponseEntity.notFound().build();
        }
        channelProfileRepository.deleteById(id);
        return ResponseEntity.ok().build();
    }
}

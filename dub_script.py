from pydub import AudioSegment
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import texttospeech
from google.cloud import translate_v2 as translate
from google.cloud import storage
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip
from moviepy.video.tools.subtitles import SubtitlesClip, TextClip
import os
import shutil
import time
import json
import tempfile
import uuid
from dotenv import load_dotenv
import fire
import html

# Load environment variables from .env file
load_dotenv()

# Set Google Cloud credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\\Dev\\Repos\\Service accounts\\multichoice-poc-430811-31d3e494115e.json"

# Function to load configuration from a JSON file
def load_config(config_file):
    with open(config_file, 'r') as f:
        return json.load(f)

# Function to decode audio from input file to WAV format
def decode_audio(inFile, outFile):
    """Extracts audio from the video file.

    Args:
        inFile (String): i.e. path to file
        outFile (String): i.e. path to
    """
    print("Decode Audio")
    if not outFile.endswith(".wav"):
        outFile += ".wav"
    AudioSegment.from_file(inFile).set_channels(
        1).export(outFile, format="wav")

# Function to get transcripts from Google Cloud Speech-to-Text API
def get_transcripts_json(gcsPath, langCode, phraseHints=[], speakerCount=1, enhancedModel=None):
    """Transcribes audio files.

    Args:
        gcsPath (String): path to file in cloud storage (i.e. "gs://audio/clip.mp4")
        langCode (String): language code (i.e. "en")
        phraseHints (String[]): list of words that are unusual but likely to appear in the audio file.
        speakerCount (int, optional): Number of speakers in the audio. Only works on English. Defaults to None.
        enhancedModel (String, optional): Option to use an enhanced speech model, i.e. "video"

    Returns:
        list | Operation.error
    """
    def _jsonify(result):
        print("jsonify")
        json_result = []
        for section in result.results:
            data = {
                "transcript": section.alternatives[0].transcript,
                "words": []
            }
            for word in section.alternatives[0].words:
                data["words"].append({
                    "word": word.word,
                    "start_time": word.start_time.total_seconds(),
                    "end_time": word.end_time.total_seconds(),
                    "speaker_tag": word.speaker_tag
                })
            json_result.append(data)
        return json_result

    client = speech.SpeechClient()
    audio = speech.RecognitionAudio(uri=gcsPath)
    diarizationConfig = speech.SpeakerDiarizationConfig(
        enable_speaker_diarization=speakerCount > 1,
    )
    if langCode == "en":
        enhancedModel = "video"
    config = speech.RecognitionConfig(
        language_code="en-US" if langCode == "en" else langCode,
        enable_automatic_punctuation=True,
        enable_word_time_offsets=True,
        speech_contexts=[{
            "phrases": phraseHints,
            "boost": 15
        }],
        diarization_config=diarizationConfig,
        profanity_filter=True,
        use_enhanced=bool(enhancedModel),
        model="video" if enhancedModel else None
    )
    res = client.long_running_recognize(config=config, audio=audio).result()

    return _jsonify(res)

# Function to parse sentences with speaker tags from transcript JSON
def parse_sentence_with_speaker(json_data, lang):
    """This is used to ensure that we sync dialog with speakers lip movement. 
    Takes json from get_transcripts_json and breaks it into sentences
    spoken by a single person. Sentences deliniated by a >= 1 second pause/

    Args:
        json (string[]): [{"transcript": "lalala", "words": [{"word": "la", "start_time": 20, "end_time": 21, "speaker_tag: 2}]}]
        lang (string): language code, i.e. "en"
    Returns:
        string[]: [{"sentence": "lalala", "speaker": 1, "start_time": 20, "end_time": 21}]
    """
    def get_word(word, lang):
        if lang == "ja":
            return word.split('|')[0]
        return word

    sentences = []
    sentence = {}
    for result in json_data:
        for i, word in enumerate(result['words']):
            wordText = get_word(word['word'], lang)
            if not sentence:
                sentence = {
                    lang: [wordText],
                    'speaker': word['speaker_tag'],
                    'start_time': word['start_time'],
                    'end_time': word['end_time']
                }
            elif word['speaker_tag'] != sentence['speaker']:
                sentence[lang] = ' '.join(sentence[lang])
                sentences.append(sentence)
                sentence = {
                    lang: [wordText],
                    'speaker': word['speaker_tag'],
                    'start_time': word['start_time'],
                    'end_time': word['end_time']
                }
            else:
                sentence[lang].append(wordText)
                sentence['end_time'] = word['end_time']
            if i + 1 < len(result['words']) and word['end_time'] < result['words'][i + 1]['start_time']:
                sentence[lang] = ' '.join(sentence[lang])
                sentences.append(sentence)
                sentence = {}
        if sentence:
            sentence[lang] = ' '.join(sentence[lang])
            sentences.append(sentence)
            sentence = {}

    return sentences

# Function to translate text using Google Cloud Translation API
def translate_text(input_text, targetLang, sourceLang=None):
    """Translates from sourceLang to targetLang. If sourceLang is empty,
    it will be auto-detected.
    RR: I have not figured out how to determine if the speaker is male / female and appending that 
    as metadata in the transcripts.

    Args:
        sentence (String): Sentence to translate
        targetLang (String): i.e. "en"
        sourceLang (String, optional): i.e. "es" Defaults to None.

    Returns:
        String: translated text
    """
    translate_client = translate.Client()
    result = translate_client.translate(
        input_text, target_language=targetLang, source_language=sourceLang)

    return html.unescape(result['translatedText'])

# Function to synthesize speech using Google Cloud Text-to-Speech API
def speak(text, languageCode, voiceName=None, speakingRate=1):
    """Converts text to audio
    RR: in this API, you can select the voice name. if we are able to generate metadata for the 
    speakers gender, we could loop through this and generate in piecemeal with different gendered voices. 
    Args:
        text (String): Text to be spoken
        languageCode (String): Language (i.e. "en")
        voiceName: (String, optional): See https://cloud.google.com/text-to-speech/docs/voices
        speakingRate: (int, optional): speed up or slow down speaking
    Returns:
        bytes : Audio in wav format
    """
    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    if not voiceName:
        voice = texttospeech.VoiceSelectionParams(
            language_code=languageCode, ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
    else:
        voice = texttospeech.VoiceSelectionParams(
            language_code=languageCode, name=voiceName
        )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speakingRate
    )
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    return response.audio_content

# Function to adjust speech duration to fit within a specified duration
def speakUnderDuration(text, languageCode, durationSecs, voiceName=None):
    """Speak text within a certain time limit.
    If audio already fits within duratinSecs, no changes will be made. If not the speed of 
    the translated audio will be adjusted to fit within the segment to allow it to sync with
    lip movements. 

    Args:
        text (String): Text to be spoken
        languageCode (String): language code, i.e. "en"
        durationSecs (int): Time limit in seconds
        voiceName (String, optional): See https://cloud.google.com/text-to-speech/docs/voices

    Returns:
        bytes : Audio in wav format
    """
    baseAudio = speak(text, languageCode, voiceName=voiceName)
    assert len(baseAudio)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        temp_filename = f.name
        f.write(baseAudio)
        f.flush()
    try:
        baseDuration = AudioSegment.from_mp3(temp_filename).duration_seconds
    finally:
        os.remove(temp_filename)
    
    ratio = baseDuration / durationSecs

    if ratio <= 1:
        return baseAudio

    ratio = round(ratio, 1)
    if ratio > 4:
        ratio = 4
    return speak(text, languageCode, voiceName=voiceName, speakingRate=ratio)

# Function to convert transcripts to SRT format
def toSrt(transcripts, charsPerLine=60):
    """Converts transcripts to SRT an SRT file. Only intended to work
    with English.

    Args:
        transcripts ({}): Transcripts returned from Speech API
        charsPerLine (int): max number of chars to write per line

    Returns:
        String srt data
    """

    """
    SRT files have this format:

    [Section of subtitles number]

    [Time the subtitle is displayed begins] –> [Time the subtitle is displayed ends]

    [Subtitle]

    Timestamps are in the format:

    [hours]: [minutes]: [seconds], [milliseconds]

    Note: about 60 characters comfortably fit on one line
    for resolution 1920x1080 with font size 40 pt.
    """
    def _srtTime(seconds):
        millisecs = seconds * 1000
        seconds, millisecs = divmod(millisecs, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02},{int(millisecs):03}"

    def _toSrt(words, startTime, endTime, index):
        return f"{index}\n" + _srtTime(startTime) + " --> " + _srtTime(endTime) + f"\n{words}"

    startTime = None
    sentence = ""
    srt = []
    index = 1
    for word in [word for x in transcripts for word in x['words']]:
        if not startTime:
            startTime = word['start_time']

        sentence += " " + word['word']

        if len(sentence) > charsPerLine:
            srt.append(_toSrt(sentence.strip(), startTime, word['end_time'], index))
            index += 1
            sentence = ""
            startTime = None

    if len(sentence):
        srt.append(_toSrt(sentence.strip(), startTime, word['end_time'], index))

    return '\n\n'.join(srt)

# Function to stitch audio segments together and overlay them on the video
def stitch_audio(sentences, audioDir, movieFile, outFile, srtPath=None, overlayGain=-30):
    """Combines sentences, audio clips, and video file into the ultimate dubbed video

    Args:
        sentences (list): Output of parse_sentence_with_speaker
        audioDir (String): Directory containing generated audio files to stitch together
        movieFile (String): Path to movie file to dub.
        outFile (String): Where to write dubbed movie.
        srtPath (String, optional): Path to transcript/srt file, if desired.
        overlayGain (int, optional): How quiet to make source audio when overlaying dubs. 
            Defaults to -30.

    Returns:
       void : Writes movie file to outFile path
    """
    audioFiles = os.listdir(audioDir)
    audioFiles.sort(key=lambda x: int(x.split('.')[0]))
    segments = [AudioSegment.from_mp3(
        os.path.join(audioDir, x)) for x in audioFiles]
    dubbed = AudioSegment.from_file(movieFile)
    for sentence, segment in zip(sentences, segments):
        dubbed = dubbed.overlay(
            segment, position=sentence['start_time'] * 1000, gain_during_overlay=overlayGain)
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as audioFile:
        temp_audio_filename = audioFile.name
        dubbed.export(temp_audio_filename, format="mp3")
    
    try:
        clip = VideoFileClip(movieFile)
        audio = AudioFileClip(temp_audio_filename)
        clip = clip.set_audio(audio)
        if srtPath:
            width, height = clip.size[0] * 0.75, clip.size[1] * 0.20
            def generator(txt): return TextClip(txt, font='Georgia-Regular',
                                                size=[width, height], color='black', method="caption")
            subtitles = SubtitlesClip(
                srtPath, generator).set_pos(("center", "bottom"))
            clip = CompositeVideoClip([clip, subtitles])
        clip.write_videofile(outFile, codec='libx264', audio_codec='aac')
    finally:
        os.remove(temp_audio_filename)

# Main function to handle the dubbing process
def dub(config_file):
    config = load_config(config_file)
    videoFile = config["videoFile"]
    outputDir = config["outputDir"]
    srcLang = config["srcLang"]
    targetLangs = config["targetLangs"]
    storageBucket = config["storageBucket"]
    phraseHints = config["phraseHints"]
    dubSrc = config["dubSrc"]
    speakerCount = config["speakerCount"]
    voices = config["voices"]
    srt = config["srt"]
    newDir = config["newDir"]
    genAudio = config["genAudio"]
    noTranslate = config["noTranslate"]

    print("Dub function")

    baseName = os.path.split(videoFile)[-1].split('.')[0]
    if newDir:
        shutil.rmtree(outputDir, ignore_errors=True)

    os.makedirs(outputDir, exist_ok=True)
    outputFiles = os.listdir(outputDir)

    if not f"{baseName}.wav" in outputFiles:
        print("Extracting audio from video")
        fn = os.path.join(outputDir, baseName + ".wav")
        decode_audio(videoFile, fn)
        print(f"Wrote {fn}")

    if not f"transcript.json" in outputFiles:
        storageBucket = storageBucket if storageBucket else os.getenv('STORAGE_BUCKET')
        if not storageBucket:
            raise Exception("Specify variable STORAGE_BUCKET in .env or as an arg")

        print("Transcribing audio")
        print("Uploading to the cloud...")
        storage_client = storage.Client()
        bucket = storage_client.bucket(storageBucket)

        tmpFile = os.path.join("tmp", str(uuid.uuid4()) + ".wav")
        blob = bucket.blob(tmpFile)
        blob.upload_from_filename(os.path.join(outputDir, baseName + ".wav"), content_type="audio/wav")

        print("Transcribing...")
        transcripts = get_transcripts_json(
            f"gs://{storageBucket}/{tmpFile}", srcLang,
            phraseHints=phraseHints,
            speakerCount=speakerCount
        )
        with open(os.path.join(outputDir, "transcript.json"), "w") as f:
            json.dump(transcripts, f)

        sentences = parse_sentence_with_speaker(transcripts, srcLang)
        with open(os.path.join(outputDir, baseName + ".json"), "w") as f:
            json.dump(sentences, f)
        print(f"Wrote {os.path.join(outputDir, baseName + '.json')}")
        print("Deleting cloud file...")
        blob.delete()

    srtPath = os.path.join(outputDir, "subtitles.srt") if srt else None
    if srt:
        transcripts = json.load(
            open(os.path.join(outputDir, "transcript.json")))
        subtitles = toSrt(transcripts)
        with open(srtPath, "w") as f:
            f.write(subtitles)
        print(f"Wrote srt subtitles to {srtPath}")

    sentences = json.load(open(os.path.join(outputDir, baseName + ".json")))
    sentence = sentences[0]

    if not noTranslate:
        for lang in targetLangs:
            print(f"Translating to {lang}")
            for sentence in sentences:
                sentence[lang] = translate_text(
                    sentence[srcLang], lang, srcLang)

        with open(os.path.join(outputDir, baseName + ".json"), "w") as f:
            json.dump(sentences, f)

    audioDir = os.path.join(outputDir, "audioClips")
    if "audioClips" not in outputFiles:
        os.mkdir(audioDir)

    if dubSrc:
        targetLangs += [srcLang]

    for lang in targetLangs:
        languageDir = os.path.join(audioDir, lang)
        if os.path.exists(languageDir):
            if not genAudio:
                continue
            shutil.rmtree(languageDir)
        os.mkdir(languageDir)
        print(f"Synthesizing audio for {lang}")
        for i, sentence in enumerate(sentences):
            voiceName = voices[lang] if lang in voices else None
            audio = speakUnderDuration(
                sentence[lang], lang, sentence['end_time'] -
                sentence['start_time'],
                voiceName=voiceName)
            with open(os.path.join(languageDir, f"{i}.mp3"), 'wb') as f:
                f.write(audio)

    dubbedDir = os.path.join(outputDir, "dubbedVideos")
    os.makedirs(dubbedDir, exist_ok=True)

    for lang in targetLangs:
        print(f"Dubbing audio for {lang}")
        outFile = os.path.join(dubbedDir, lang + ".mp4")
        stitch_audio(sentences, os.path.join(audioDir, lang), videoFile, outFile, srtPath=srtPath)

    print("Done")

# Entry point for the script using Fire to parse command line arguments
if __name__ == "__main__":
    fire.Fire(dub)

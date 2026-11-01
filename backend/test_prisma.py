from services.prisma_service import transcribe_audio


def main():
    print("===== VoiceKirana - Gnani Prisma Test =====")

    audio_path = input(
        "Enter the full path of your audio file: "
    ).strip().strip('"')

    try:
        print("\nSending audio to Gnani Prisma...")
        transcript = transcribe_audio(audio_path)

        print("\n===== TRANSCRIPTION RESULT =====")
        print(transcript)
        print("================================")

    except Exception as error:
        print("\nSomething went wrong:")
        print(error)


if __name__ == "__main__":
    main()

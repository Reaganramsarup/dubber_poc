# Google Dubbing Example for the Multichoice POC

In this directory, you'll find code that takes a movie and:
This repo contains the code that uses various google services to translate and dub a video for any language of your choosing. 

Follow the setps below to get this up and running. 

1. Since this uses google cloud, you would need to create a google cloud account and then create a project. 

2. The following API's are used for the translation bit and then we use python to do the dubbing:

- Speech-to-Text
- Text-to-Speech
- Translation

These can be enabled in the the console or by running them in the cloud cli:
        
3. Create a new service account and give it roles for translation, speech to text and text to speech permissions, You would then need to download the keyfile if you are running this locally to allow authentication from your local machine:

4. Create a new Google Cloud Storage bucket. We'll need this to store data temporarily while interacting with the Speech API:

5. 6. Create a virtualenv:

        python -m venv venv

6. Edit `.env`, and adjust to your project id and bucket.

7. Install the python dependencies:

        pip install -r requirements.txt

8. Adjust the config file to point to your directories, source and target languages etc. You will also need to adjust any directories referenced directly in the code. 

8. Run with this command through terminal: python dub_script.py config.json

import logging
from .config import Settings
from .application import Application
from .api import Api
from .http import MediaHttpServer


def main():
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger().setLevel(logging.INFO)
    settings = Settings.from_env()
    with Application(settings) as app:
        with MediaHttpServer((settings.host, settings.port), Api(app)) as server:
            logging.info("声画织场服务: http://%s:%s/loom/v1/", settings.host, settings.port)
            try:
                server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt:
                logging.info("Stopping render service")


if __name__ == "__main__":
    main()

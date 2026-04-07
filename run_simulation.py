import multiprocessing
import hydra
import logging
from omegaconf import OmegaConf
from supervisory.supervisory_model import SupervisoryModel
from adapters import AdapterWorker


def run_worker(model_name: str, config_dict: dict):
    AdapterWorker.from_config(model_name, OmegaConf.create(config_dict)).run()


@hydra.main(version_base=None, config_path="config", config_name="config")
def main(config):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler()],
    )

    config_dict = OmegaConf.to_container(config, resolve=True)

    for model_name in config.models.keys():
        multiprocessing.Process(
            target=run_worker, args=(model_name, config_dict)
        ).start()

    model = SupervisoryModel.from_config(config)
    if getattr(config.simulation, "reset_tables", False):
        model.reset_state_memory(drop_tables=True)
    model.run()


if __name__ == "__main__":
    main()

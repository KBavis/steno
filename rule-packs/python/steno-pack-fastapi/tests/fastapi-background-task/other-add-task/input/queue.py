class TaskQueue:
    def add_task(self, fn):
        ...


queue = TaskQueue()


def schedule():
    queue.add_task(print)      # add_task on something that isn't BackgroundTasks

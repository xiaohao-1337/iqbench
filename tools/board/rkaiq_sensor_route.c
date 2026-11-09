/* Resolve the active sensor on this board's shared RKISP main path. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <linux/media.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int next_entity(int fd, struct media_entity_desc *entity)
{
    entity->id |= MEDIA_ENT_ID_FLAG_NEXT;
    return ioctl(fd, MEDIA_IOC_ENUM_ENTITIES, entity) == 0;
}

static struct media_link_desc *entity_links(int fd, const struct media_entity_desc *entity)
{
    if (!entity->links)
        return NULL;
    struct media_link_desc *links = calloc(entity->links, sizeof(*links));
    if (!links)
        return NULL;
    struct media_links_enum request = {.entity = entity->id, .links = links};
    if (ioctl(fd, MEDIA_IOC_ENUM_LINKS, &request) != 0) {
        free(links);
        return NULL;
    }
    return links;
}

static int find_cif_input(char *name, size_t size)
{
    int fd = open("/dev/media2", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    int count = 0;
    struct media_entity_desc isp = {0};
    while (next_entity(fd, &isp) && strcmp(isp.name, "rkisp-isp-subdev") != 0) {}
    struct media_entity_desc source = {0};
    while (strcmp(isp.name, "rkisp-isp-subdev") == 0 && next_entity(fd, &source)) {
        if (strncmp(source.name, "rkcif-mipi-lvds", sizeof("rkcif-mipi-lvds") - 1) != 0)
            continue;
        struct media_link_desc *links = entity_links(fd, &source);
        for (unsigned i = 0; links && i < source.links; ++i) {
            const struct media_link_desc *link = &links[i];
            if (link->source.entity == source.id && link->sink.entity == isp.id &&
                link->sink.index == 0 && (link->flags & MEDIA_LNK_FL_ENABLED)) {
                snprintf(name, size, "%s", source.name);
                ++count;
            }
        }
        free(links);
    }
    close(fd);
    return count == 1;
}

static int find_sensor(const char *cif, char *name, size_t size)
{
    int count = 0;
    for (int index = 0; index < 2; ++index) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/media%d", index);
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0)
            continue;
        struct media_device_info info = {0};
        if (ioctl(fd, MEDIA_IOC_DEVICE_INFO, &info) == 0 && strcmp(info.model, cif) == 0) {
            struct media_entity_desc sensor = {0};
            while (next_entity(fd, &sensor)) {
                if (sensor.type != MEDIA_ENT_F_CAM_SENSOR)
                    continue;
                struct media_link_desc *links = entity_links(fd, &sensor);
                for (unsigned i = 0; links && i < sensor.links; ++i) {
                    if (links[i].source.entity == sensor.id && (links[i].flags & MEDIA_LNK_FL_ENABLED)) {
                        snprintf(name, size, "%s", sensor.name);
                        ++count;
                        break;
                    }
                }
                free(links);
            }
        }
        close(fd);
    }
    return count == 1;
}

const char *rk_aiq_uapi2_sysctl_getBindedSnsEntNmByVd(const char *video)
{
    const char *(*original)(const char *) = dlsym(RTLD_NEXT, "rk_aiq_uapi2_sysctl_getBindedSnsEntNmByVd");
    const char *default_sensor = original ? original(video) : NULL;
    if (!video || strcmp(video, "/dev/video22") != 0)
        return default_sensor;

    static _Thread_local char active_sensor[32];
    char cif[32];
    if (!find_cif_input(cif, sizeof(cif))) {
        fprintf(stderr, "RKAIQ route: could not resolve one CIF input for %s\n", video);
        return NULL;
    }
    if (!find_sensor(cif, active_sensor, sizeof(active_sensor))) {
        fprintf(stderr, "RKAIQ route: could not resolve one active sensor on %s\n", cif);
        return NULL;
    }
    fprintf(stderr, "RKAIQ route: %s -> %s -> %s\n", video, cif, active_sensor);
    return active_sensor;
}
